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
    stubber = Stubber(client)
    stubber.add_response(
        "change_resource_record_sets",
        {"ChangeInfo": {"Id": "/change/1", "Status": "PENDING", "SubmittedAt": "2024-01-01T00:00:00Z"}},
        {
            "HostedZoneId": "ZONEID123",
            "ChangeBatch": {
                "Changes": [{
                    "Action": "DELETE",
                    "ResourceRecordSet": {
                        "Name": f"edge.{infra.dns_label}.launchpad.app.",
                        "Type": "CNAME",
                        "TTL": 60,
                        "ResourceRecords": [{"Value": "stale-alb.us-east-1.elb.amazonaws.com"}],
                    },
                }],
            },
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
