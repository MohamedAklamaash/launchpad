"""Backfill for rows persisted before write-time redaction: rewrites raw Environment and
Database text, leaves fixed points and empty fields alone, and writes nothing on --dry-run."""
import uuid
from io import StringIO

import pytest
from api.services.log_redaction import redact_provisioning_text
from django.core.management import call_command

SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
RAW_LOGS = f"[COMMAND]\nprovider config: secret_key = {SECRET_KEY}\nmodule.vpc.aws_vpc.main: Creating...\n"
RAW_ERROR = f"Terraform execution failed: key {SECRET_KEY}"
CLEAN_LOGS = redact_provisioning_text(RAW_LOGS).text


@pytest.fixture
def make_env(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, logs=None, error_message=None):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", metadata={},
        )
        return Environment.objects.create(
            infrastructure=infra, status="ERROR", logs=logs, error_message=error_message,
        )
    return _make


def _make_database(env, *, error_message):
    from api.models.database import Database

    return Database.objects.create(
        environment=env, name="primary-db", engine="postgres", engine_version="16.6",
        instance_class="db.t3.micro", allocated_storage=20, status="ERROR", error_message=error_message,
    )


def _run(*args):
    out = StringIO()
    call_command("redact_stored_provisioning_text", *args, stdout=out)
    return out.getvalue()


def test_rewrites_raw_environment_and_database_rows(make_env):
    env = make_env(logs=RAW_LOGS, error_message=RAW_ERROR)
    database = _make_database(env, error_message=RAW_ERROR)
    before = env.updated_at

    output = _run()

    env.refresh_from_db()
    database.refresh_from_db()
    assert SECRET_KEY not in env.logs
    assert SECRET_KEY not in env.error_message
    assert SECRET_KEY not in database.error_message
    assert env.logs == CLEAN_LOGS
    assert env.updated_at == before
    assert "Environment: scanned 1, rewrote 1" in output
    assert "Database: scanned 1, rewrote 1" in output


def test_is_idempotent_and_skips_clean_rows(make_env):
    clean = make_env(logs=CLEAN_LOGS, error_message=None)
    raw = make_env(logs=RAW_LOGS)

    first = _run()
    second = _run()

    clean.refresh_from_db()
    raw.refresh_from_db()
    assert clean.logs == CLEAN_LOGS
    assert clean.error_message is None
    assert raw.logs == CLEAN_LOGS
    assert "Environment: scanned 2, rewrote 1" in first
    assert "Environment: scanned 2, rewrote 0" in second


def test_dry_run_writes_nothing(make_env):
    env = make_env(logs=RAW_LOGS, error_message=RAW_ERROR)
    database = _make_database(env, error_message=RAW_ERROR)

    output = _run("--dry-run")

    env.refresh_from_db()
    database.refresh_from_db()
    assert env.logs == RAW_LOGS
    assert env.error_message == RAW_ERROR
    assert database.error_message == RAW_ERROR
    assert "Environment: scanned 1, would rewrite 1" in output
    assert "Database: scanned 1, would rewrite 1" in output
