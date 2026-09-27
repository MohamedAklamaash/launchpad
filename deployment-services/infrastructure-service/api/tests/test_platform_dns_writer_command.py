"""dispatch.handle_delivery — the ack/discard/retry/DLQ decision run_dns_writer's
on_message adapts into actual pika channel calls. Tested as a plain function (see
dispatch.py's module docstring for why on_message's closure itself isn't a good unit-test
surface), plus one end-to-end command-startup test for the PLATFORM_BASE_DOMAIN gate.
"""
import json
import uuid
from unittest.mock import patch

import pytest
from api.services.platform_dns.dispatch import handle_delivery


@pytest.fixture
def make_infra(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, alb_dns="internal-abc.us-east-1.elb.amazonaws.com"):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            is_mock=True, metadata={"aws_region": "us-east-1"},
        )
        infra.mint_dns_label()
        Environment.objects.create(infrastructure=infra, alb_dns=alb_dns, status="ACTIVE")
        return infra

    return _make


@pytest.fixture(autouse=True)
def _reset_fake_zone():
    from api.services.platform_dns.route53_client import reset_fake_zone_for_tests
    reset_fake_zone_for_tests()
    yield
    reset_fake_zone_for_tests()


def test_success_acks(make_infra):
    infra = make_infra()
    body = json.dumps({"infrastructure_id": str(infra.id), "attempt": 0}).encode()

    outcome = handle_delivery(body, dev_mode=True, max_attempts=5)

    assert outcome.action == "ack"


def test_malformed_message_discards():
    outcome = handle_delivery(b"not json", dev_mode=True, max_attempts=5)
    assert outcome.action == "discard"


def test_validation_error_discards_not_retries(make_infra):
    infra = make_infra(alb_dns="attacker-bucket.s3.amazonaws.com")
    body = json.dumps({"infrastructure_id": str(infra.id), "attempt": 0}).encode()

    outcome = handle_delivery(body, dev_mode=True, max_attempts=5)

    assert outcome.action == "discard"


def test_transient_failure_retries_with_incremented_attempt(make_infra):
    infra = make_infra()
    body = json.dumps({"infrastructure_id": str(infra.id), "attempt": 1}).encode()

    with patch(
        "api.services.platform_dns.dispatch.converge_from_infra_id",
        side_effect=RuntimeError("transient DB blip"),
    ):
        outcome = handle_delivery(body, dev_mode=True, max_attempts=5)

    assert outcome.action == "retry"
    assert outcome.infra_id == str(infra.id)
    assert outcome.next_attempt == 2


def test_transient_failure_exhausted_goes_to_dlq(make_infra):
    infra = make_infra()
    max_attempts = 3
    body = json.dumps({"infrastructure_id": str(infra.id), "attempt": max_attempts - 1}).encode()

    with patch(
        "api.services.platform_dns.dispatch.converge_from_infra_id",
        side_effect=RuntimeError("still failing"),
    ):
        outcome = handle_delivery(body, dev_mode=True, max_attempts=max_attempts)

    assert outcome.action == "dlq"
    assert outcome.infra_id == str(infra.id)


def test_run_dns_writer_refuses_to_start_in_prod_without_explicit_base_domain(monkeypatch):
    """R2: PLATFORM_BASE_DOMAIN must be explicitly set in the environment — the
    settings.PLATFORM_BASE_DOMAIN fallback to 'launchpad.app' must not satisfy this."""
    from unittest.mock import MagicMock

    from django.core.management import call_command
    from django.core.management.base import CommandError

    monkeypatch.delenv("PLATFORM_BASE_DOMAIN", raising=False)
    for var in ("PLATFORM_DNS_ZONE_ID", "PLATFORM_DNS_ACCOUNT_ID",
                "PLATFORM_DNS_ACCESS_KEY_ID", "PLATFORM_DNS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(var, "x")
    monkeypatch.setattr(
        "api.common.envs.application.app_config",
        MagicMock(is_dns_writer=True, mode="prod"),
    )
    with pytest.raises(CommandError, match="PLATFORM_BASE_DOMAIN"):
        call_command("run_dns_writer")
