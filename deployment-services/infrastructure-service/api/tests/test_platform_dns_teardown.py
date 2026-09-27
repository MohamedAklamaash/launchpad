"""Teardown ordering, ledger survival across hard delete, and the mock/real gate —
F1b part 1 pre-review §3 and §6 items 2/3/5.
"""
import uuid
from unittest.mock import patch

import pytest
from api.models.platform_dns_record import PlatformDnsRecord


@pytest.fixture
def make_infra(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, env_status="ACTIVE", alb_dns="internal-abc.us-east-1.elb.amazonaws.com"):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            is_cloud_authenticated=True, metadata={"aws_region": "us-east-1"},
        )
        infra.mint_dns_label()
        Environment.objects.create(infrastructure=infra, status=env_status, alb_dns=alb_dns)
        return infra

    return _make


# ---- ledger survives a hard delete of Infrastructure ----

def test_ledger_row_survives_infrastructure_hard_delete(make_infra):
    from api.models.infrastructure import Infrastructure

    infra = make_infra()
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind="edge",
        record_name=f"edge.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="internal-abc.us-east-1.elb.amazonaws.com", ttl=60,
    )

    Infrastructure.objects.filter(id=infra.id).delete()

    assert not Infrastructure.objects.filter(id=infra.id).exists()
    assert PlatformDnsRecord.objects.filter(infrastructure_id=infra.id).exists()


def test_desired_state_is_empty_after_hard_delete_even_with_a_live_ledger_row(make_infra):
    """After a hard delete there is no dns_label to read from Infrastructure any more —
    desired state must resolve to empty (not error), leaving the ledger row as the only
    thing telling a future sweep/teardown pass what still needs deleting."""
    from api.models.infrastructure import Infrastructure
    from api.services.platform_dns.desired_state import compute_desired_state

    infra = make_infra()
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind="edge",
        record_name=f"edge.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="internal-abc.us-east-1.elb.amazonaws.com", ttl=60,
    )
    Infrastructure.objects.filter(id=infra.id).delete()

    assert compute_desired_state(str(infra.id)) == []


# ---- delete_infrastructure refuses hard delete while ledger is live ----

def test_delete_infrastructure_refuses_while_ledger_row_live(make_infra):
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(env_status="DESTROYED")
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind="edge",
        record_name=f"edge.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="internal-abc.us-east-1.elb.amazonaws.com", ttl=60,
    )

    with pytest.raises(ValueError, match="platform DNS"):
        InfrastructureService().delete_infrastructure(infra.user_id, infra.id)

    from api.models.infrastructure import Infrastructure
    assert Infrastructure.objects.filter(id=infra.id).exists()


def test_delete_infrastructure_refuses_while_tls_requested_at_set_even_with_empty_ledger(make_infra):
    from api.models.infrastructure_certificate import InfrastructureCertificate
    from api.services.infrastructure import InfrastructureService
    from django.utils import timezone

    infra = make_infra(env_status="DESTROYED")
    InfrastructureCertificate.objects.create(infrastructure=infra, tls_requested_at=timezone.now())

    with pytest.raises(ValueError, match="platform DNS"):
        InfrastructureService().delete_infrastructure(infra.user_id, infra.id)


def test_delete_infrastructure_succeeds_once_ledger_is_empty(make_infra):
    from api.services.infrastructure import InfrastructureService

    infra = make_infra(env_status="DESTROYED")
    with patch("api.messaging.producer.producer.infra_producer"):
        result = InfrastructureService().delete_infrastructure(infra.user_id, infra.id)

    assert result is True
    from api.models.infrastructure import Infrastructure
    assert not Infrastructure.objects.filter(id=infra.id).exists()


# ---- destroy() runs DNS teardown before AssumeRole, and stays DESTROYING if either fails ----

def test_destroy_stays_destroying_when_dns_teardown_not_confirmed(make_infra):
    from api.models.environment import Environment
    from api.services.terraform_worker import TerraformWorker

    infra = make_infra()
    with patch("api.services.terraform_worker.request_and_await_dns_teardown") as teardown, \
            patch("api.services.terraform_worker.authenticate_infrastructure") as assume_role:
        from api.services.platform_dns.teardown import DnsTeardownPending
        teardown.side_effect = DnsTeardownPending("writer unavailable")
        TerraformWorker.destroy(str(infra.id))

    teardown.assert_called_once()
    assume_role.assert_not_called()
    env = Environment.objects.get(infrastructure_id=infra.id)
    assert env.status == "DESTROYING"


def test_destroy_runs_dns_teardown_before_assume_role_and_stays_destroying_on_assume_role_failure(make_infra):
    from api.models.environment import Environment
    from api.services.terraform_worker import TerraformWorker

    infra = make_infra()
    call_order = []

    def _teardown_ok(infra_id, **kwargs):
        call_order.append("dns_teardown")

    def _assume_role_fails(infra):
        call_order.append("assume_role")
        raise RuntimeError("customer deleted the deployment role")

    with patch("api.services.terraform_worker.request_and_await_dns_teardown", side_effect=_teardown_ok), \
            patch("api.services.terraform_worker.authenticate_infrastructure", side_effect=_assume_role_fails):
        TerraformWorker.destroy(str(infra.id))

    assert call_order == ["dns_teardown", "assume_role"]
    env = Environment.objects.get(infrastructure_id=infra.id)
    assert env.status == "DESTROYING"


# ---- mock/real gate ----

def test_get_route53_client_refuses_mock_infra_outside_dev_mode():
    from api.services.platform_dns.route53_client import (
        MockRealMismatch,
        get_route53_client,
    )

    with pytest.raises(MockRealMismatch):
        get_route53_client(infra_is_mock=True, dev_mode=False)


def test_get_route53_client_refuses_real_infra_inside_dev_mode():
    from api.services.platform_dns.route53_client import (
        MockRealMismatch,
        get_route53_client,
    )

    with pytest.raises(MockRealMismatch):
        get_route53_client(infra_is_mock=False, dev_mode=True)


def test_get_route53_client_refuses_unconfigured_zone_in_production(monkeypatch):
    from api.services.platform_dns.route53_client import (
        PlatformDnsMisconfigured,
        get_route53_client,
    )

    for var in (
        "PLATFORM_DNS_ZONE_ID", "PLATFORM_DNS_ACCOUNT_ID",
        "PLATFORM_DNS_ACCESS_KEY_ID", "PLATFORM_DNS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(PlatformDnsMisconfigured):
        get_route53_client(infra_is_mock=False, dev_mode=False)


def test_get_route53_client_returns_fake_zone_for_mock_infra_in_dev_mode():
    from api.services.platform_dns.route53_client import (
        FakeRoute53Zone,
        get_route53_client,
        reset_fake_zone_for_tests,
    )

    reset_fake_zone_for_tests()
    client, _zone_id = get_route53_client(infra_is_mock=True, dev_mode=True)
    assert isinstance(client, FakeRoute53Zone)
    reset_fake_zone_for_tests()
