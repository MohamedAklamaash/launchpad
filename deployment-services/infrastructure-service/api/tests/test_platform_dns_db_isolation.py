"""R1: the dns_writer role connects to Postgres with its own least-privilege credential,
not the shared DATABASE_USER_NAME/PASSWORD every other process uses.
"""
import pytest
from api.common.envs.database import DatabaseConfig
from django.core.management import call_command


def _base_env(monkeypatch):
    monkeypatch.setenv("DATABASE_USER_NAME", "shared_user")
    monkeypatch.setenv("DATABASE_PASSWORD", "shared_password")
    monkeypatch.setenv("DATABASE_NAME", "infrastructure_db")
    monkeypatch.setenv("INFRASTRUCTURE_DB_URL", "postgres://x:y@localhost/z")
    monkeypatch.delenv("DNS_WRITER_DB_USER", raising=False)
    monkeypatch.delenv("DNS_WRITER_DB_PASSWORD", raising=False)


def test_non_writer_role_uses_shared_credential(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("LAUNCHPAD_PROCESS_ROLE", raising=False)

    config = DatabaseConfig.from_env()

    assert config.user_name == "shared_user"
    assert config.password == "shared_password"


def test_writer_role_uses_dedicated_credential_when_set(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LAUNCHPAD_PROCESS_ROLE", "dns_writer")
    monkeypatch.setenv("DNS_WRITER_DB_USER", "launchpad_dns_writer")
    monkeypatch.setenv("DNS_WRITER_DB_PASSWORD", "writer_password")

    config = DatabaseConfig.from_env()

    assert config.user_name == "launchpad_dns_writer"
    assert config.password == "writer_password"


def test_writer_role_refuses_to_start_in_production_without_dedicated_credential(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LAUNCHPAD_PROCESS_ROLE", "dns_writer")
    monkeypatch.setenv("MODE", "prod")

    with pytest.raises(RuntimeError, match="DNS_WRITER_DB"):
        DatabaseConfig.from_env()


def test_writer_role_falls_back_to_shared_credential_in_dev_mode_only(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LAUNCHPAD_PROCESS_ROLE", "dns_writer")
    monkeypatch.setenv("MODE", "dev")

    config = DatabaseConfig.from_env()

    assert config.user_name == "shared_user"
    assert config.password == "shared_password"


def test_apply_dns_writer_grants_is_a_noop_warning_on_non_postgres(db):
    # The test backend is sqlite — this must warn and return, never attempt to run
    # postgres-only DO $$ ... $$ syntax against it.
    call_command("apply_dns_writer_grants")
