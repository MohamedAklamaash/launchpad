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


def test_delete_infrastructure_allowed_with_empty_ledger_even_if_tls_was_ever_requested(make_infra):
    """R7 fix: tls_requested_at is monotonic and never cleared (by design). Gating hard
    delete on it, in addition to the ledger, would make any infra that ever requested a
    certificate permanently undeletable — even long after its DNS records were torn down.
    Only a live ledger row may block a hard delete."""
    from api.models.infrastructure_certificate import InfrastructureCertificate
    from api.services.infrastructure import InfrastructureService
    from django.utils import timezone

    infra = make_infra(env_status="DESTROYED")
    InfrastructureCertificate.objects.create(infrastructure=infra, tls_requested_at=timezone.now())

    with patch("api.messaging.producer.producer.infra_producer"):
        result = InfrastructureService().delete_infrastructure(infra.user_id, infra.id)

    assert result is True
    from api.models.infrastructure import Infrastructure
    assert not Infrastructure.objects.filter(id=infra.id).exists()


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


def _stubbed_route53_client():
    import boto3
    from botocore.stub import Stubber

    client = boto3.client(
        "route53", region_name="us-east-1",
        aws_access_key_id="AKIAFAKE", aws_secret_access_key="fake",
    )
    return client, Stubber(client)


def test_assert_zone_matches_base_domain_accepts_matching_zone():
    from api.services.platform_dns.route53_client import (
        PlatformDnsZoneConfig,
        assert_zone_matches_base_domain,
    )

    config = PlatformDnsZoneConfig(
        base_domain="launchpad.app", zone_id="Z123", account_id="111111111111",
        access_key_id="a", secret_access_key="b",
    )
    client, stubber = _stubbed_route53_client()
    stubber.add_response(
        "get_hosted_zone",
        {"HostedZone": {"Id": "Z123", "Name": "launchpad.app.", "CallerReference": "x"}},
        {"Id": "Z123"},
    )
    stubber.activate()
    assert_zone_matches_base_domain(client, config)
    stubber.assert_no_pending_responses()
    stubber.deactivate()


def test_assert_zone_matches_base_domain_rejects_mismatched_zone():
    from api.services.platform_dns.route53_client import (
        PlatformDnsMisconfigured,
        PlatformDnsZoneConfig,
        assert_zone_matches_base_domain,
    )

    config = PlatformDnsZoneConfig(
        base_domain="launchpad.app", zone_id="Z123", account_id="111111111111",
        access_key_id="a", secret_access_key="b",
    )
    client, stubber = _stubbed_route53_client()
    stubber.add_response(
        "get_hosted_zone",
        {"HostedZone": {"Id": "Z123", "Name": "some-other-domain.example.", "CallerReference": "x"}},
        {"Id": "Z123"},
    )
    stubber.activate()
    with pytest.raises(PlatformDnsMisconfigured):
        assert_zone_matches_base_domain(client, config)
    stubber.deactivate()


def test_assert_caller_identity_requires_exact_arn_not_suffix_match():
    """R2/RECOMMENDED: exact ARN comparison, not endswith — a suffix match would also
    accept an ARN this credential is not supposed to have."""
    from api.services.platform_dns.route53_client import (
        PlatformDnsMisconfigured,
        PlatformDnsZoneConfig,
        assert_caller_identity,
    )

    config = PlatformDnsZoneConfig(
        base_domain="launchpad.app", zone_id="Z123", account_id="111111111111",
        access_key_id="a", secret_access_key="b",
    )
    import boto3
    from botocore.stub import Stubber

    sts_client = boto3.client("sts", region_name="us-east-1", aws_access_key_id="a", aws_secret_access_key="b")
    stubber = Stubber(sts_client)
    stubber.add_response(
        "get_caller_identity",
        {
            "Account": "111111111111",
            # A different (but suffix-matching) user path — exact-match must reject this.
            "Arn": "arn:aws:iam::111111111111:user/path/user/launchpad-platform-dns-writer",
            "UserId": "AIDA123",
        },
    )
    stubber.activate()
    with patch("boto3.client", return_value=sts_client), pytest.raises(PlatformDnsMisconfigured):
        assert_caller_identity(config)
    stubber.deactivate()


# ---- R6: cross-tenant isolation end-to-end ----

def test_forged_reconcile_for_infra_a_never_touches_infra_bs_ledger(make_infra, monkeypatch):
    """A reconcile message naming infra A's real UUID (the only thing a forged/replayed
    message could ever carry — there is no name/value in the message to forge) must
    converge only infra A's own records. Proven end-to-end: two infras, two different edge
    targets, converge one, assert the other's ledger and desired zone state are untouched.
    """
    from api.services.platform_dns.converge import converge_platform_dns
    from api.services.platform_dns.route53_client import reset_fake_zone_for_tests

    reset_fake_zone_for_tests()
    infra_a = make_infra(alb_dns="internal-a.us-east-1.elb.amazonaws.com")
    infra_b = make_infra(alb_dns="internal-b.us-east-1.elb.amazonaws.com")

    converge_platform_dns(str(infra_a.id), infra_is_mock=True, dev_mode=True)

    a_records = PlatformDnsRecord.objects.filter(infrastructure_id=infra_a.id)
    b_records = PlatformDnsRecord.objects.filter(infrastructure_id=infra_b.id)
    assert a_records.exists()
    assert not b_records.exists()
    assert all(infra_b.dns_label not in row.record_name for row in a_records)
    reset_fake_zone_for_tests()


# ---- R6: reaper-parked-ERROR infra can still be re-destroyed despite a live ledger row ----

def test_delete_infrastructure_reenqueues_destroy_for_reaper_parked_error_despite_live_ledger(make_infra):
    """A DESTROYING environment the reaper parks in ERROR after MAX_REAP_ATTEMPTS (e.g.
    because DNS teardown never confirmed) still has 'once activated' history —
    delete_infrastructure's ACTIVE/ERROR-with-history branch must re-enqueue destroy, not
    fall into the ledger-guarded immediate-delete branch meant for PENDING/DESTROYED/
    never-provisioned rows. The ledger guard belongs only to that other branch."""
    from api.models.environment import Environment
    from api.services.infrastructure import InfrastructureService
    from django.utils import timezone

    infra = make_infra(env_status="ERROR")
    Environment.objects.filter(infrastructure_id=infra.id).update(first_activated_at=timezone.now())
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind="edge",
        record_name=f"edge.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="internal-abc.us-east-1.elb.amazonaws.com", ttl=60,
    )

    with patch("api.services.infrastructure.InfraQueue") as Q:
        Q.enqueue_destroy.return_value = True
        result = InfrastructureService().delete_infrastructure(infra.user_id, infra.id)

    assert result is True
    Q.enqueue_destroy.assert_called_once_with(str(infra.id))
    from api.models.infrastructure import Infrastructure
    assert Infrastructure.objects.filter(id=infra.id).exists()
    env = Environment.objects.get(infrastructure_id=infra.id)
    assert env.status == "DESTROYING"


# ---- RECOMMENDED: refusing a hard delete republishes a reconcile ----

def test_run_worker_refusal_republishes_reconcile(make_infra):
    from api.management.commands.run_worker import _refuse_hard_delete_for_live_dns

    infra = make_infra()
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind="edge",
        record_name=f"edge.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="internal-abc.us-east-1.elb.amazonaws.com", ttl=60,
    )

    with patch("api.services.platform_dns.producer.request_dns_reconcile") as republish:
        refused = _refuse_hard_delete_for_live_dns(str(infra.id))

    assert refused is True
    republish.assert_called_once_with(str(infra.id), coalesce=False)


def test_run_worker_no_refusal_when_ledger_empty(make_infra):
    from api.management.commands.run_worker import _refuse_hard_delete_for_live_dns

    infra = make_infra()
    with patch("api.services.platform_dns.producer.request_dns_reconcile") as republish:
        refused = _refuse_hard_delete_for_live_dns(str(infra.id))

    assert refused is False
    republish.assert_not_called()
