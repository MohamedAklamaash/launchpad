"""api/services/host_readiness.py — the cross-service "is host routing live" snapshot
computation and publish (F1b part 3a)."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from api.models.platform_dns_record import PlatformDnsRecord
from api.services import host_readiness
from shared.enums.orchestrator import ComputeType


@pytest.fixture
def make_infra(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(compute_type=ComputeType.ECS_FARGATE):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012", compute_type=compute_type,
        )
        infra.mint_dns_label()
        return infra

    return _make


# ── is_dns_synced ────────────────────────────────────────────────────────────────────────

def test_dns_not_synced_when_no_records_exist(make_infra):
    infra = make_infra()
    assert host_readiness.is_dns_synced(infra.id) is False


def test_dns_not_synced_when_only_one_of_edge_wildcard_is_synced(make_infra):
    infra = make_infra()
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind=PlatformDnsRecord.KIND_EDGE,
        record_name=f"edge.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="x.elb.amazonaws.com", ttl=60, synced_at="2024-01-01T00:00:00Z",
    )
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind=PlatformDnsRecord.KIND_WILDCARD,
        record_name=f"*.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value=f"edge.{infra.dns_label}.launchpad.app", ttl=300, synced_at=None,
    )
    assert host_readiness.is_dns_synced(infra.id) is False


def test_dns_not_synced_when_only_validation_record_exists(make_infra):
    """A validation-only record (before the edge/wildcard pair exists) never counts."""
    infra = make_infra()
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind=PlatformDnsRecord.KIND_VALIDATION,
        record_name=f"_abc.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="_x.y.acm-validations.aws.", ttl=300, synced_at="2024-01-01T00:00:00Z",
    )
    assert host_readiness.is_dns_synced(infra.id) is False


def test_dns_synced_when_both_edge_and_wildcard_are_synced(make_infra):
    infra = make_infra()
    for kind, name in (
        (PlatformDnsRecord.KIND_EDGE, f"edge.{infra.dns_label}.launchpad.app"),
        (PlatformDnsRecord.KIND_WILDCARD, f"*.{infra.dns_label}.launchpad.app"),
    ):
        PlatformDnsRecord.objects.create(
            infrastructure_id=infra.id, kind=kind, record_name=name, record_type="CNAME",
            record_value="x", ttl=60, synced_at="2024-01-01T00:00:00Z",
        )
    assert host_readiness.is_dns_synced(infra.id) is True


# ── https_ready_for ──────────────────────────────────────────────────────────────────────

def test_https_ready_false_without_an_environment():
    infra = SimpleNamespace(compute_type=ComputeType.ECS_FARGATE)
    assert host_readiness.https_ready_for(infra, None) is False


def test_https_ready_for_ecs_uses_listener_arn():
    infra = SimpleNamespace(compute_type=ComputeType.ECS_FARGATE)
    assert host_readiness.https_ready_for(infra, SimpleNamespace(https_listener_arn=None, eks_ingress_tls_ready=True)) is False
    assert host_readiness.https_ready_for(infra, SimpleNamespace(https_listener_arn="arn:x", eks_ingress_tls_ready=False)) is True


def test_https_ready_for_eks_uses_ingress_tls_ready_not_listener_arn():
    infra = SimpleNamespace(compute_type=ComputeType.EKS)
    assert host_readiness.https_ready_for(infra, SimpleNamespace(https_listener_arn="arn:x", eks_ingress_tls_ready=False)) is False
    assert host_readiness.https_ready_for(infra, SimpleNamespace(https_listener_arn=None, eks_ingress_tls_ready=True)) is True


# ── publish_host_readiness ───────────────────────────────────────────────────────────────

def test_publish_host_readiness_is_a_noop_for_a_missing_infra(db, monkeypatch):
    producer = MagicMock()
    monkeypatch.setattr("api.messaging.producer.producer.infra_producer", producer)

    host_readiness.publish_host_readiness(str(uuid.uuid4()))

    producer.publish_host_readiness_updated.assert_not_called()


def test_publish_host_readiness_publishes_current_snapshot(make_infra, monkeypatch):
    from api.models.environment import Environment
    from api.models.infrastructure_certificate import InfrastructureCertificate

    infra = make_infra()
    Environment.objects.create(infrastructure=infra, status="ACTIVE", https_listener_arn="arn:https")
    InfrastructureCertificate.objects.create(infrastructure=infra, tls_status=InfrastructureCertificate.TLS_ISSUED)

    producer = MagicMock()
    monkeypatch.setattr("api.messaging.producer.producer.infra_producer", producer)

    host_readiness.publish_host_readiness(str(infra.id))

    producer.publish_host_readiness_updated.assert_called_once_with(
        infra_id=str(infra.id), dns_label=infra.dns_label,
        tls_status=InfrastructureCertificate.TLS_ISSUED, dns_synced=False, https_ready=True,
        version=1,
    )
    infra.refresh_from_db()
    assert infra.host_readiness_version == 1


def test_publish_host_readiness_version_is_monotonic_across_calls(make_infra, monkeypatch):
    """RECOMMENDED item 2 (security review): the per-infra counter strictly increases on
    every publish, regardless of what else changed — this is what lets the consumer order
    events without trusting wall clocks."""
    infra = make_infra()
    producer = MagicMock()
    monkeypatch.setattr("api.messaging.producer.producer.infra_producer", producer)

    host_readiness.publish_host_readiness(str(infra.id))
    host_readiness.publish_host_readiness(str(infra.id))
    host_readiness.publish_host_readiness(str(infra.id))

    versions = [call.kwargs["version"] for call in producer.publish_host_readiness_updated.call_args_list]
    assert versions == [1, 2, 3]
    infra.refresh_from_db()
    assert infra.host_readiness_version == 3


def test_publish_host_readiness_never_raises_when_producer_fails(make_infra, monkeypatch):
    producer = MagicMock()
    producer.publish_host_readiness_updated.side_effect = RuntimeError("broker down")
    monkeypatch.setattr("api.messaging.producer.producer.infra_producer", producer)

    infra = make_infra()
    host_readiness.publish_host_readiness(str(infra.id))  # must not raise
