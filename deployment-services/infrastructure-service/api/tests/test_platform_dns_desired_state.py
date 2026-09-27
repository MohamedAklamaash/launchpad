"""Desired-state derivation — the core of F1b part 1's cross-tenant isolation.

The reconcile message carries only {infrastructure_id}; every name/value the writer ever
touches comes from this module reading the DB row for that one id. These tests are the
"forged/replayed reconcile writes nothing outside that infra's label" guard from the
pre-review's test list item 1: since there is no name in the message to forge, the
equivalent attack is a row whose *stored* alb_dns/validation data points somewhere it
shouldn't — and that must be refused here, before Route53 is ever called.
"""
import uuid

import pytest
from api.services.platform_dns import naming
from api.services.platform_dns.desired_state import compute_desired_state


@pytest.fixture
def make_infra(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, region="us-east-1", alb_dns=None, teardown_requested=False):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            metadata={"aws_region": region},
        )
        infra.mint_dns_label()
        if teardown_requested:
            infra.mark_dns_teardown_requested()
        if alb_dns:
            from api.models.environment import Environment
            Environment.objects.create(infrastructure=infra, alb_dns=alb_dns, status="ACTIVE")
        return infra

    return _make


def test_empty_when_infra_does_not_exist(db):
    assert compute_desired_state(str(uuid.uuid4())) == []


def test_empty_when_dns_label_never_minted(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User
    user = User.objects.create(id=uuid.uuid4(), email="u@e.io", user_name="u", role="super_admin")
    infra = Infrastructure.objects.create(
        user=user, name="i", cloud_provider="aws", max_cpu=1.0, max_memory=2.0,
    )
    assert compute_desired_state(str(infra.id)) == []


@pytest.mark.parametrize("env_status", ["DESTROYING", "ERROR", "ACTIVE"])
def test_empty_when_teardown_requested_even_with_live_alb_dns(make_infra, env_status):
    """dns_teardown_requested_at, not Environment.status, is the signal — a reaper that
    rewrites DESTROYING to ERROR (see run_worker.py's MAX_REAP_ATTEMPTS path) must not
    resurrect desired state for an infra whose teardown was already requested."""
    from api.models.environment import Environment

    infra = make_infra(alb_dns="internal-abc.us-east-1.elb.amazonaws.com", teardown_requested=True)
    Environment.objects.filter(infrastructure_id=infra.id).update(status=env_status)
    assert compute_desired_state(str(infra.id)) == []


def test_edge_and_wildcard_present_when_alb_dns_set(make_infra):
    infra = make_infra(alb_dns="internal-abc.us-east-1.elb.amazonaws.com")
    records = compute_desired_state(str(infra.id))
    kinds = {r.kind for r in records}
    assert kinds == {"edge", "wildcard"}
    edge = next(r for r in records if r.kind == "edge")
    wildcard = next(r for r in records if r.kind == "wildcard")
    assert edge.name == f"edge.{infra.dns_label}.launchpad.app"
    assert edge.value == "internal-abc.us-east-1.elb.amazonaws.com"
    assert wildcard.name == f"*.{infra.dns_label}.launchpad.app"
    assert wildcard.value == edge.name  # wildcard points at edge, never straight at the ALB


def test_no_edge_when_alb_dns_unset(make_infra):
    infra = make_infra()
    assert compute_desired_state(str(infra.id)) == []


def test_rejects_edge_target_in_wrong_region(make_infra):
    infra = make_infra(region="us-east-1", alb_dns="internal-abc.us-west-2.elb.amazonaws.com")
    with pytest.raises(naming.InvalidDnsRecordError):
        compute_desired_state(str(infra.id))


def test_rejects_edge_target_that_is_not_an_elb_hostname(make_infra):
    infra = make_infra(alb_dns="attacker-bucket.s3.amazonaws.com")
    with pytest.raises(naming.InvalidDnsRecordError):
        compute_desired_state(str(infra.id))


def test_validation_record_included_only_when_cert_row_fully_populated(make_infra):
    from api.models.infrastructure_certificate import InfrastructureCertificate

    infra = make_infra(alb_dns="internal-abc.us-east-1.elb.amazonaws.com")
    InfrastructureCertificate.objects.create(infrastructure=infra)  # validation fields blank
    assert {r.kind for r in compute_desired_state(str(infra.id))} == {"edge", "wildcard"}

    InfrastructureCertificate.objects.filter(infrastructure=infra).update(
        validation_name=f"_a79865eb4cd1a6ab990a45779c92cf6f.{infra.dns_label}.launchpad.app",
        validation_value="_a79865eb4cd1a6ab990a45779c92cf6f.xlfgrmvvlj.acm-validations.aws.",
    )
    kinds = {r.kind for r in compute_desired_state(str(infra.id))}
    assert kinds == {"edge", "wildcard", "validation"}


def test_rejects_validation_record_scoped_to_another_infras_label(make_infra):
    """The cross-tenant case: a cert row whose validation_name was somehow written for a
    different infra's label must never reach Route53 for this infra."""
    from api.models.infrastructure_certificate import InfrastructureCertificate

    victim = make_infra(alb_dns="internal-abc.us-east-1.elb.amazonaws.com")
    attacker = make_infra(alb_dns="internal-xyz.us-east-1.elb.amazonaws.com")
    InfrastructureCertificate.objects.create(
        infrastructure=victim,
        validation_name=f"_a79865eb4cd1a6ab990a45779c92cf6f.{attacker.dns_label}.launchpad.app",
        validation_value="_a79865eb4cd1a6ab990a45779c92cf6f.xlfgrmvvlj.acm-validations.aws.",
    )
    with pytest.raises(naming.InvalidDnsRecordError):
        compute_desired_state(str(victim.id))


def test_rejects_validation_value_missing_acm_suffix(make_infra):
    from api.models.infrastructure_certificate import InfrastructureCertificate

    infra = make_infra(alb_dns="internal-abc.us-east-1.elb.amazonaws.com")
    InfrastructureCertificate.objects.create(
        infrastructure=infra,
        validation_name=f"_a79865eb4cd1a6ab990a45779c92cf6f.{infra.dns_label}.launchpad.app",
        validation_value="attacker.controlled.value.",
    )
    with pytest.raises(naming.InvalidDnsRecordError):
        compute_desired_state(str(infra.id))
