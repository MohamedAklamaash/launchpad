"""Compute the DNS records an infrastructure should have, straight from the DB.

The reconcile message carries only {infrastructure_id} (see producer.py) — no name, no
value, no "create" vs "delete" intent. Every name and value the writer ever hands to
Route53 is derived here, from rows this service already trusts, never from the message or
from any other caller-supplied input. A forged or replayed reconcile message therefore
cannot do anything except re-derive this same infrastructure's own state.

Desired state is empty (nothing should exist) when the Infrastructure row is gone (hard
deleted) or when `dns_teardown_requested_at` is set. That field, not Environment.status, is
the signal: status is rewritten by the reaper (see run_worker.py's MAX_REAP_ATTEMPTS path)
and is unset entirely once the Environment row itself is deleted, so it is unsafe to gate a
destructive decision on it. dns_teardown_requested_at is monotonic and set once, before the
first teardown reconcile is even published — see teardown.py.
"""
from dataclasses import dataclass

from api.mock.aws_fixtures import resolve_region
from api.models.environment import Environment
from api.models.infrastructure import Infrastructure
from api.models.infrastructure_certificate import InfrastructureCertificate

from . import naming


@dataclass(frozen=True, slots=True)
class DesiredRecord:
    kind: str
    name: str
    record_type: str
    value: str
    ttl: int


def compute_desired_state(infrastructure_id: str) -> list[DesiredRecord]:
    infra = Infrastructure.objects.filter(id=infrastructure_id).first()
    if infra is None or infra.dns_teardown_requested_at is not None:
        return []
    if not infra.dns_label:
        return []

    label = infra.dns_label
    base_domain = _base_domain()
    records: list[DesiredRecord] = []

    env = Environment.objects.filter(infrastructure_id=infrastructure_id).first()
    if env is not None and env.alb_dns:
        region = resolve_region(infra)
        naming.assert_valid_edge_target(env.alb_dns, region)
        edge_name = naming.edge_record_name(label, base_domain)
        naming.assert_owned_record_name(edge_name, label, base_domain, kind="edge")
        records.append(DesiredRecord(
            kind="edge", name=edge_name, record_type="CNAME", value=env.alb_dns, ttl=60,
        ))

        wildcard_name = naming.wildcard_record_name(label, base_domain)
        naming.assert_owned_record_name(wildcard_name, label, base_domain, kind="wildcard")
        records.append(DesiredRecord(
            kind="wildcard", name=wildcard_name, record_type="CNAME", value=edge_name, ttl=300,
        ))

    cert = InfrastructureCertificate.objects.filter(infrastructure_id=infrastructure_id).first()
    if cert is not None and cert.validation_name and cert.validation_value:
        validation_name = naming.normalize_route53_name(cert.validation_name)
        naming.assert_owned_record_name(validation_name, label, base_domain, kind="validation")
        naming.assert_valid_validation_value(cert.validation_value)
        records.append(DesiredRecord(
            kind="validation", name=validation_name, record_type="CNAME",
            value=cert.validation_value, ttl=300,
        ))

    for record in records:
        naming.assert_two_labels_below_apex(record.name, base_domain)

    return records


def _base_domain() -> str:
    from django.conf import settings
    return settings.PLATFORM_BASE_DOMAIN
