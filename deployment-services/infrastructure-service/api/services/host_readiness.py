"""Computes and publishes the "is this infra's host routing actually live" signal that
application-service's read-model needs to gate the `host_url` it shows a customer (F1b part
3a — see plan/F1b-tls-activation.md).

Deliberately narrow: every function here only reads rows this service already owns
(InfrastructureCertificate, PlatformDnsRecord, Environment) and publishes a fresh snapshot.
The consumer on the receiving end (application-service) must never trust this payload for
authorization — it only mirrors read-model fields, exactly like every other infra event
consumer in this codebase.
"""
import logging

from api.models.platform_dns_record import PlatformDnsRecord
from shared.enums.orchestrator import ComputeType

logger = logging.getLogger(__name__)


def is_dns_synced(infra_id) -> bool:
    """True only once BOTH the edge and wildcard records for this infra are confirmed
    INSYNC. A lone validation record (present before the first successful apply, or after
    the edge/wildcard pair has been torn down) never counts — there is nothing for a host URL
    to resolve against without both of those."""
    rows = list(
        PlatformDnsRecord.objects.filter(
            infrastructure_id=infra_id,
            kind__in=[PlatformDnsRecord.KIND_EDGE, PlatformDnsRecord.KIND_WILDCARD],
        ).values_list("kind", "synced_at")
    )
    present_kinds = {kind for kind, _synced_at in rows}
    if present_kinds != {PlatformDnsRecord.KIND_EDGE, PlatformDnsRecord.KIND_WILDCARD}:
        return False
    return all(synced_at is not None for _kind, synced_at in rows)


def https_ready_for(infra, env) -> bool:
    """Normalizes the two compute types' different "443 is actually applied" signals into
    one boolean: ECS's is the terraform-applied listener ARN, EKS's is the IngressClassParams
    patch (see api/services/eks_bootstrap.py:apply_eks_tls)."""
    if env is None:
        return False
    if infra.compute_type == ComputeType.EKS:
        return bool(env.eks_ingress_tls_ready)
    return bool(env.https_listener_arn)


def publish_host_readiness(infra_id) -> None:
    """Best-effort snapshot publish. Always re-reads current DB state rather than trusting
    any caller-held values (which may already be stale by the time this runs) — safe to call
    from any number of trigger points (a terraform apply, the TLS re-check tick, the DNS
    writer's own converge loop) since it always reports the true current state regardless of
    which trigger fired it. Never raises: none of those callers should fail because a
    read-model mirror publish failed.

    RECOMMENDED item 2 (security review): `Infrastructure.host_readiness_version` is
    incremented under a row lock before every publish, and the resulting integer travels
    with the event as its ordering key — application-service's consumer rejects any
    payload missing it, rather than the previous wall-clock `occurred_at` comparison,
    which several concurrent publishers on potentially different machines could skew.
    """
    from api.messaging.producer.producer import infra_producer
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.infrastructure_certificate import InfrastructureCertificate
    from django.db import transaction

    try:
        with transaction.atomic():
            infra = Infrastructure.objects.select_for_update().filter(id=infra_id).first()
            if infra is None:
                return
            infra.host_readiness_version = infra.host_readiness_version + 1
            infra.save(update_fields=["host_readiness_version"])
            version = infra.host_readiness_version

        env = Environment.objects.filter(infrastructure_id=infra_id).first()
        cert = InfrastructureCertificate.objects.filter(infrastructure_id=infra_id).first()
        infra_producer.publish_host_readiness_updated(
            infra_id=infra_id,
            dns_label=infra.dns_label,
            tls_status=cert.tls_status if cert else None,
            dns_synced=is_dns_synced(infra_id),
            https_ready=https_ready_for(infra, env),
            version=version,
        )
    except Exception:
        logger.warning("publish_host_readiness failed for %s (non-fatal)", infra_id, exc_info=True)
