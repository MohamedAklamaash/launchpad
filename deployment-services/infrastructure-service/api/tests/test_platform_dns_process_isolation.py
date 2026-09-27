"""Process-role isolation — F1b part 1 pre-review §1 BLOCK item.

The dns_writer role must never hold the platform's cross-account keys (JWT_SECRET,
INTERNAL_API_TOKEN, AWS_ACCESS_KEY_ID/SECRET); every other process must never hold the
platform DNS writer's credential. Both directions are asserted at startup, not merely
documented, because a silent misconfiguration here concentrates exactly the blast radius
this design exists to avoid.
"""
from unittest.mock import MagicMock, patch

import pytest

ALL_FOUR_FORBIDDEN = ("JWT_SECRET", "INTERNAL_API_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def _base_env(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET", "x" * 60)
    monkeypatch.setenv("DJANGO_PORT", "8002")
    monkeypatch.delenv("LAUNCHPAD_PROCESS_ROLE", raising=False)
    for var in (*ALL_FOUR_FORBIDDEN, "PLATFORM_DNS_ACCESS_KEY_ID", "PLATFORM_DNS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("forbidden_var", ALL_FOUR_FORBIDDEN)
def test_dns_writer_role_refuses_to_start_if_platform_credential_present(monkeypatch, forbidden_var):
    from api.common.envs.application import ApplicationConfig

    _base_env(monkeypatch)
    monkeypatch.setenv("LAUNCHPAD_PROCESS_ROLE", "dns_writer")
    monkeypatch.setenv(forbidden_var, "leaked-value")

    with pytest.raises(RuntimeError, match="refusing to start"):
        ApplicationConfig.from_env()


def test_dns_writer_role_starts_with_all_four_absent(monkeypatch):
    from api.common.envs.application import ApplicationConfig

    _base_env(monkeypatch)
    monkeypatch.setenv("LAUNCHPAD_PROCESS_ROLE", "dns_writer")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

    config = ApplicationConfig.from_env()
    assert config.is_dns_writer is True
    assert config.jwt_secret == ""
    assert config.internal_api_token == ""
    assert config.aws_access_key_id == ""
    assert config.aws_secret_access_key == ""


@pytest.mark.parametrize("platform_dns_var", ["PLATFORM_DNS_ACCESS_KEY_ID", "PLATFORM_DNS_SECRET_ACCESS_KEY"])
def test_non_writer_role_refuses_to_start_if_platform_dns_credential_present(monkeypatch, platform_dns_var):
    from api.common.envs.application import ApplicationConfig

    _base_env(monkeypatch)
    for var in ALL_FOUR_FORBIDDEN:
        monkeypatch.setenv(var, "x" * 40 if "TOKEN" in var or "SECRET" in var else "AKIA" + "x" * 16)
    monkeypatch.setenv(platform_dns_var, "leaked-value")

    with pytest.raises(RuntimeError, match="refusing to start"):
        ApplicationConfig.from_env()


def test_application_service_refuses_to_start_if_platform_dns_credential_present(monkeypatch):
    from shared.process_role import assert_no_platform_dns_credentials

    monkeypatch.setenv("PLATFORM_DNS_SECRET_ACCESS_KEY", "leaked-value")
    with pytest.raises(RuntimeError, match="refusing to start"):
        assert_no_platform_dns_credentials("application-service")


def test_run_dns_writer_refuses_to_start_when_not_dns_writer_role(monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    monkeypatch.setattr(
        "api.common.envs.application.app_config", MagicMock(is_dns_writer=False),
    )
    with pytest.raises(CommandError, match="dns_writer"):
        call_command("run_dns_writer")


def test_run_dns_writer_refuses_to_start_in_prod_with_zone_unconfigured(monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    for var in (
        "PLATFORM_DNS_ZONE_ID", "PLATFORM_DNS_ACCOUNT_ID",
        "PLATFORM_DNS_ACCESS_KEY_ID", "PLATFORM_DNS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "api.common.envs.application.app_config",
        MagicMock(is_dns_writer=True, mode="prod"),
    )
    with pytest.raises(CommandError, match="PLATFORM_DNS"):
        call_command("run_dns_writer")


def test_apps_ready_starts_no_consumer_threads_for_dns_writer_role():
    from django.apps import apps

    api_app_config = apps.get_app_config("api")
    fake_app_config = MagicMock(is_dns_writer=True, mode="prod")
    with patch("api.common.envs.application.app_config", fake_app_config), \
            patch("shared.mode.enforce_dev_mode_safety"), \
            patch("threading.Thread") as fake_thread:
        api_app_config.ready()
    fake_thread.assert_not_called()
