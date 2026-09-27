"""sync_platform_dns_changes — the periodic out-of-process GetChange catch-up command
(RECOMMENDED item 1, security review)."""
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


def test_refuses_to_run_outside_the_dns_writer_role(db, monkeypatch):
    monkeypatch.delenv("LAUNCHPAD_PROCESS_ROLE", raising=False)
    with pytest.raises(CommandError, match="dns_writer"):
        call_command("sync_platform_dns_changes")


def test_confirms_pending_rows_against_the_mock_zone(db, dns_writer_env, monkeypatch):
    from api.common.envs.application import ApplicationConfig

    monkeypatch.setattr("api.common.envs.application.app_config", ApplicationConfig.from_env())

    PlatformDnsRecord.objects.create(
        infrastructure_id=uuid.uuid4(), kind="edge",
        record_name="edge.0123456789abcdef.launchpad.app", record_type="CNAME",
        record_value="x.us-east-1.elb.amazonaws.com", ttl=60, change_id="/change/MOCK",
    )

    call_command("sync_platform_dns_changes")

    row = PlatformDnsRecord.objects.get(record_name="edge.0123456789abcdef.launchpad.app")
    assert row.synced_at is not None
    assert row.change_id is None


def test_is_a_noop_with_no_pending_rows(db, dns_writer_env, monkeypatch):
    from api.common.envs.application import ApplicationConfig

    monkeypatch.setattr("api.common.envs.application.app_config", ApplicationConfig.from_env())

    call_command("sync_platform_dns_changes")  # must not raise
