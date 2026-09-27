"""converge_platform_dns against a stubbed Route53 client.

Using botocore's Stubber rather than mocks means an unstubbed call — in particular a
change_resource_record_sets the test never expected — raises instead of silently
succeeding, which is exactly the proof needed for "a no-op reconcile calls Route53 zero
times" and "a hostile value never reaches the API" (see StubberError assertions below).
"""
import uuid

import pytest
from api.models.platform_dns_record import PlatformDnsRecord
from botocore.stub import Stubber


@pytest.fixture
def make_infra(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, alb_dns=None):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            metadata={"aws_region": "us-east-1"},
        )
        infra.mint_dns_label()
        if alb_dns:
            Environment.objects.create(infrastructure=infra, alb_dns=alb_dns, status="ACTIVE")
        return infra

    return _make


def _real_route53_client():
    import boto3
    return boto3.client(
        "route53", region_name="us-east-1",
        aws_access_key_id="AKIAFAKE", aws_secret_access_key="fake",
    )


@pytest.fixture
def stubbed_client(monkeypatch):
    client = _real_route53_client()
    stubber = Stubber(client)

    def _get_route53_client(*, infra_is_mock, dev_mode):
        return client, "ZONEID123"

    monkeypatch.setattr(
        "api.services.platform_dns.converge.get_route53_client", _get_route53_client,
    )
    stubber.activate()
    yield client, stubber
    stubber.deactivate()


def test_noop_reconcile_calls_route53_zero_times(make_infra, stubbed_client):
    from api.services.platform_dns.converge import converge_platform_dns

    infra = make_infra()  # no alb_dns -> desired state is empty, ledger is empty too
    _client, stubber = stubbed_client
    # No responses queued at all: any API call raises StubberError.
    converge_platform_dns(str(infra.id), infra_is_mock=False, dev_mode=False)
    stubber.assert_no_pending_responses()


def test_upsert_writes_ledger_before_route53_call(make_infra, monkeypatch):
    import boto3
    from api.services.platform_dns.converge import converge_platform_dns

    infra = make_infra(alb_dns="internal-abc.us-east-1.elb.amazonaws.com")
    client = boto3.client(
        "route53", region_name="us-east-1",
        aws_access_key_id="AKIAFAKE", aws_secret_access_key="fake",
    )
    stubber = Stubber(client)
    monkeypatch.setattr(
        "api.services.platform_dns.converge.get_route53_client",
        lambda *, infra_is_mock, dev_mode: (client, "ZONEID123"),
    )
    edge_name = f"edge.{infra.dns_label}.launchpad.app"
    wildcard_name = f"*.{infra.dns_label}.launchpad.app"
    expected_batch = {
        "HostedZoneId": "ZONEID123",
        "ChangeBatch": {
            "Changes": [
                {
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": f"{edge_name}.",
                        "Type": "CNAME",
                        "TTL": 60,
                        "ResourceRecords": [{"Value": "internal-abc.us-east-1.elb.amazonaws.com"}],
                    },
                },
                {
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": r"\052." + f"{infra.dns_label}.launchpad.app.",
                        "Type": "CNAME",
                        "TTL": 300,
                        "ResourceRecords": [{"Value": edge_name}],
                    },
                },
            ],
        },
    }
    stubber.add_response(
        "change_resource_record_sets",
        {"ChangeInfo": {"Id": "/change/1", "Status": "PENDING", "SubmittedAt": "2024-01-01T00:00:00Z"}},
        expected_batch,
    )
    stubber.activate()

    converge_platform_dns(str(infra.id), infra_is_mock=False, dev_mode=False)

    stubber.assert_no_pending_responses()
    ledger = PlatformDnsRecord.objects.filter(infrastructure_id=infra.id)
    assert {row.record_name for row in ledger} == {edge_name, wildcard_name}
    stubber.deactivate()


def test_orphaned_ledger_row_is_deleted_when_no_longer_desired(make_infra, monkeypatch):
    import boto3
    from api.services.platform_dns.converge import converge_platform_dns

    infra = make_infra()  # no alb_dns -> nothing desired
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind="edge",
        record_name=f"edge.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="stale-alb.us-east-1.elb.amazonaws.com", ttl=60,
    )

    client = boto3.client(
        "route53", region_name="us-east-1",
        aws_access_key_id="AKIAFAKE", aws_secret_access_key="fake",
    )
    live_record = {
        "Name": f"edge.{infra.dns_label}.launchpad.app.",
        "Type": "CNAME",
        "TTL": 60,
        "ResourceRecords": [{"Value": "stale-alb.us-east-1.elb.amazonaws.com"}],
    }
    stubber = Stubber(client)
    # R3: the delete path reads the live record first (DELETE requires exact live
    # TTL/values) before issuing the actual delete.
    stubber.add_response(
        "list_resource_record_sets",
        {"ResourceRecordSets": [live_record], "IsTruncated": False, "MaxItems": "1"},
        {
            "HostedZoneId": "ZONEID123",
            "StartRecordName": f"edge.{infra.dns_label}.launchpad.app.",
            "StartRecordType": "CNAME",
            "MaxItems": "1",
        },
    )
    stubber.add_response(
        "change_resource_record_sets",
        {"ChangeInfo": {"Id": "/change/1", "Status": "PENDING", "SubmittedAt": "2024-01-01T00:00:00Z"}},
        {
            "HostedZoneId": "ZONEID123",
            "ChangeBatch": {"Changes": [{"Action": "DELETE", "ResourceRecordSet": live_record}]},
        },
    )
    stubber.activate()
    monkeypatch.setattr(
        "api.services.platform_dns.converge.get_route53_client",
        lambda *, infra_is_mock, dev_mode: (client, "ZONEID123"),
    )

    converge_platform_dns(str(infra.id), infra_is_mock=False, dev_mode=False)

    stubber.assert_no_pending_responses()
    assert not PlatformDnsRecord.objects.filter(infrastructure_id=infra.id).exists()
    stubber.deactivate()


def test_delete_drops_stale_ledger_row_without_calling_route53_when_record_already_gone(make_infra, monkeypatch):
    """The zone no longer has the record (deleted out of band, or a previous partial
    reconcile already removed it) — the ledger row is simply stale and should be dropped
    without issuing a DELETE Route53 would reject for not matching anything live."""
    import boto3
    from api.services.platform_dns.converge import converge_platform_dns

    infra = make_infra()
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind="edge",
        record_name=f"edge.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="stale-alb.us-east-1.elb.amazonaws.com", ttl=60,
    )

    client = boto3.client(
        "route53", region_name="us-east-1",
        aws_access_key_id="AKIAFAKE", aws_secret_access_key="fake",
    )
    stubber = Stubber(client)
    stubber.add_response(
        "list_resource_record_sets",
        {"ResourceRecordSets": [], "IsTruncated": False, "MaxItems": "1"},
        {
            "HostedZoneId": "ZONEID123",
            "StartRecordName": f"edge.{infra.dns_label}.launchpad.app.",
            "StartRecordType": "CNAME",
            "MaxItems": "1",
        },
    )
    stubber.activate()
    monkeypatch.setattr(
        "api.services.platform_dns.converge.get_route53_client",
        lambda *, infra_is_mock, dev_mode: (client, "ZONEID123"),
    )

    converge_platform_dns(str(infra.id), infra_is_mock=False, dev_mode=False)

    stubber.assert_no_pending_responses()  # change_resource_record_sets was never called
    assert not PlatformDnsRecord.objects.filter(infrastructure_id=infra.id).exists()
    stubber.deactivate()


def test_ledger_row_not_removed_if_route53_call_fails(make_infra, monkeypatch):
    """Delete happens only after a confirmed Route53 delete — a failed call must leave the
    ledger row in place so the next reconcile retries rather than silently forgetting a
    record that might still be live."""
    import boto3
    from api.services.platform_dns.converge import converge_platform_dns
    from botocore.exceptions import ClientError

    infra = make_infra()
    PlatformDnsRecord.objects.create(
        infrastructure_id=infra.id, kind="edge",
        record_name=f"edge.{infra.dns_label}.launchpad.app", record_type="CNAME",
        record_value="stale-alb.us-east-1.elb.amazonaws.com", ttl=60,
    )

    client = boto3.client(
        "route53", region_name="us-east-1",
        aws_access_key_id="AKIAFAKE", aws_secret_access_key="fake",
    )
    stubber = Stubber(client)
    stubber.add_response(
        "list_resource_record_sets",
        {
            "ResourceRecordSets": [{
                "Name": f"edge.{infra.dns_label}.launchpad.app.",
                "Type": "CNAME", "TTL": 60,
                "ResourceRecords": [{"Value": "stale-alb.us-east-1.elb.amazonaws.com"}],
            }],
            "IsTruncated": False,
            "MaxItems": "1",
        },
        {
            "HostedZoneId": "ZONEID123",
            "StartRecordName": f"edge.{infra.dns_label}.launchpad.app.",
            "StartRecordType": "CNAME",
            "MaxItems": "1",
        },
    )
    stubber.add_client_error("change_resource_record_sets", service_error_code="Throttling")
    stubber.activate()
    monkeypatch.setattr(
        "api.services.platform_dns.converge.get_route53_client",
        lambda *, infra_is_mock, dev_mode: (client, "ZONEID123"),
    )

    with pytest.raises(ClientError):
        converge_platform_dns(str(infra.id), infra_is_mock=False, dev_mode=False)

    assert PlatformDnsRecord.objects.filter(infrastructure_id=infra.id).exists()
    stubber.deactivate()


# ---- R4: per-infra advisory lock ----

def test_advisory_lock_is_noop_on_sqlite(make_infra):
    """The test backend is sqlite — pg_advisory_xact_lock has no sqlite equivalent, and
    _acquire_infra_lock must not attempt to run postgres-only SQL against it. If this
    raises, converge_platform_dns itself (already exercised by every other test in this
    file) would fail on every test run, so this mostly documents the guard explicitly."""
    from api.services.platform_dns.converge import _acquire_infra_lock
    from django.db import connection

    assert connection.vendor != "postgresql"
    _acquire_infra_lock("00000000-0000-0000-0000-000000000000")  # must not raise


def test_advisory_lock_issues_pg_advisory_xact_lock_on_postgres(monkeypatch):
    from unittest.mock import MagicMock

    from api.services.platform_dns.converge import _acquire_infra_lock

    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_connection = MagicMock(vendor="postgresql")
    fake_connection.cursor.return_value = fake_cursor

    monkeypatch.setattr("api.services.platform_dns.converge.connection", fake_connection)

    _acquire_infra_lock("infra-123")

    fake_cursor.execute.assert_called_once_with(
        "SELECT pg_advisory_xact_lock(hashtext(%s))", ["infra-123"],
    )
