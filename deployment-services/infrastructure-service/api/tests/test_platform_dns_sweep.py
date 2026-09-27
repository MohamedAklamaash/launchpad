"""sweep_platform_dns — orphan cleanup capped and refusing to run wild on an empty ledger."""
import uuid

import pytest
from api.models.platform_dns_record import PlatformDnsRecord
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.fixture
def dns_writer_env(monkeypatch):
    monkeypatch.setenv("LAUNCHPAD_PROCESS_ROLE", "dns_writer")
    monkeypatch.setenv("MODE", "dev")
    for var in ("JWT_SECRET", "INTERNAL_API_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_fake_zone():
    from api.services.platform_dns.route53_client import reset_fake_zone_for_tests
    reset_fake_zone_for_tests()
    yield
    reset_fake_zone_for_tests()


def _fake_client():
    from api.services.platform_dns.route53_client import get_route53_client
    client, _zone_id = get_route53_client(infra_is_mock=True, dev_mode=True)
    return client


def test_sweep_aborts_when_ledger_empty_but_zone_has_tenant_records(db, dns_writer_env, monkeypatch):
    from api.common.envs.application import ApplicationConfig

    monkeypatch.setattr(
        "api.common.envs.application.app_config", ApplicationConfig.from_env(),
    )
    client = _fake_client()
    client.change_resource_record_sets(
        HostedZoneId="MOCKZONEID",
        ChangeBatch={"Changes": [{
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": "edge.0123456789abcdef.launchpad.app.",
                "Type": "CNAME", "TTL": 60,
                "ResourceRecords": [{"Value": "orphan.us-east-1.elb.amazonaws.com"}],
            },
        }]},
    )

    with pytest.raises(CommandError, match="aborting"):
        call_command("sweep_platform_dns")


def test_sweep_deletes_orphan_not_in_ledger(db, dns_writer_env, monkeypatch):
    from api.common.envs.application import ApplicationConfig

    monkeypatch.setattr(
        "api.common.envs.application.app_config", ApplicationConfig.from_env(),
    )
    client = _fake_client()
    client.change_resource_record_sets(
        HostedZoneId="MOCKZONEID",
        ChangeBatch={"Changes": [{
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": "edge.0123456789abcdef.launchpad.app.",
                "Type": "CNAME", "TTL": 60,
                "ResourceRecords": [{"Value": "orphan.us-east-1.elb.amazonaws.com"}],
            },
        }]},
    )
    # A live ledger row for a DIFFERENT record makes the ledger non-empty, so the sweep
    # proceeds and treats the unlisted record above as the orphan to delete.
    PlatformDnsRecord.objects.create(
        infrastructure_id=uuid.uuid4(), kind="edge",
        record_name="edge.fedcba9876543210.launchpad.app", record_type="CNAME",
        record_value="kept.us-east-1.elb.amazonaws.com", ttl=60,
    )

    call_command("sweep_platform_dns")

    remaining = client.list_resource_record_sets(HostedZoneId="MOCKZONEID")["ResourceRecordSets"]
    assert all(r["Name"] != "edge.0123456789abcdef.launchpad.app." for r in remaining)


def test_sweep_refuses_when_not_dns_writer_role(db, monkeypatch):
    monkeypatch.delenv("LAUNCHPAD_PROCESS_ROLE", raising=False)
    from api.common.envs.application import ApplicationConfig
    monkeypatch.setattr(
        "api.common.envs.application.app_config", ApplicationConfig.from_env(),
    )
    with pytest.raises(CommandError, match="dns_writer"):
        call_command("sweep_platform_dns")
