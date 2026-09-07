"""Owner-only provisioning-log endpoint: the authz ladder (404 stranger, 403 invited,
404 malformed id, 404 no environment), read-time re-redaction of rows that reached the
database raw, and the drift warning that keeps the read layer from silently masking a
write-path regression."""
import logging
import uuid
from unittest.mock import MagicMock

import pytest
from api.services.log_redaction import redact_provisioning_text
from api.services.terraform_worker import MAX_LOG_CHARS, _capped_logs
from rest_framework.test import APIRequestFactory, force_authenticate

DRIFT_LOGGER = "api.services.provisioning_logs_service"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
PROGRESS_LINE = "module.vpc.aws_vpc.main: Still creating... [10s elapsed]\n"
HUGE = PROGRESS_LINE * (MAX_LOG_CHARS // len(PROGRESS_LINE) + 100)
RAW_LOGS = f"[COMMAND]\nprovider config: secret_key = {SECRET_KEY}\nmodule.vpc.aws_vpc.main: Creating...\n"
FAILED_BLOCK = (
    "╷\n"
    "│ Error: creating ECS Cluster (infra-abc): AccessDeniedException: User is not authorized "
    "to perform: ecs:CreateCluster\n"
    "│\n"
    "│   with module.ecs.aws_ecs_cluster.main,\n"
    "╵\n"
)


@pytest.fixture(autouse=True)
def _stub_infra_queue(monkeypatch):
    """database_service/infrastructure import InfraQueue at module load time, so
    patching sys.modules after that first import is a no-op — patch the
    already-bound names directly instead. The real InfraQueue opens Redis
    connection pools, which isn't available in the test environment."""
    fake = MagicMock()
    monkeypatch.setattr("api.services.database_service.InfraQueue", fake)
    monkeypatch.setattr("api.services.infrastructure.InfraQueue", fake)
    return fake


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )
    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(*, owner=None):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", metadata={},
        )
        return owner, infra
    return _make


@pytest.fixture
def make_infra_env(db, make_infra):
    from api.models.environment import Environment

    def _make(*, env_status="ACTIVE", logs=None, error_message=None):
        owner, infra = make_infra()
        env = Environment.objects.create(
            infrastructure=infra, status=env_status, vpc_id="vpc-abc123",
            logs=logs, error_message=error_message,
        )
        return owner, infra, env
    return _make


def _get_logs(factory, user, infra_id):
    from api.views.provisioning_logs import provisioning_logs

    request = factory.get(f"/api/v1/infrastructures/{infra_id}/logs/")
    force_authenticate(request, user=user)
    return provisioning_logs(request, infra_id=infra_id)


def _store_raw(env, **fields):
    from api.models.environment import Environment

    Environment.objects.filter(pk=env.pk).update(**fields)


def _drift_records(caplog):
    return [r for r in caplog.records if r.name == DRIFT_LOGGER and r.levelno == logging.WARNING]


# ── happy path ────────────────────────────────────────────────────────────────

def test_owner_gets_logs_status_and_error_message(factory, make_infra_env):
    logs = redact_provisioning_text("[COMMAND]\nmodule.vpc.aws_vpc.main: Creating...\n" + FAILED_BLOCK).text
    error = redact_provisioning_text("Terraform execution failed:\n" + FAILED_BLOCK).text
    owner, infra, _env = make_infra_env(env_status="ERROR", logs=logs, error_message=error)

    resp = _get_logs(factory, owner, str(infra.id))

    assert resp.status_code == 200
    assert resp.data["status"] == "ERROR"
    assert resp.data["logs"] == logs
    assert "ecs:CreateCluster" in resp.data["error_message"]
    assert resp.data["truncated"] is False
    assert resp.data["updated_at"] is not None


# ── authz ladder ──────────────────────────────────────────────────────────────

def test_invited_admin_gets_403(factory, make_infra_env, make_user):
    _owner, infra, _env = make_infra_env(logs="[COMMAND]")
    invited = make_user()
    infra.invited_users.add(invited)

    resp = _get_logs(factory, invited, str(infra.id))

    assert resp.status_code == 403
    assert "logs" not in resp.data


def test_cross_tenant_stranger_gets_404(factory, make_infra_env, make_user):
    _owner, infra, _env = make_infra_env(logs="[COMMAND]")

    resp = _get_logs(factory, make_user(), str(infra.id))

    assert resp.status_code == 404
    assert "logs" not in resp.data


def test_malformed_infra_id_gets_404_not_500(factory, make_user):
    resp = _get_logs(factory, make_user(), "not-a-uuid")

    assert resp.status_code == 404


def test_missing_environment_gets_404(factory, make_infra):
    owner, infra = make_infra()

    resp = _get_logs(factory, owner, str(infra.id))

    assert resp.status_code == 404


# ── read-time re-redaction ────────────────────────────────────────────────────

def test_response_is_redacted_when_stored_row_is_raw(factory, make_infra_env):
    owner, infra, env = make_infra_env(env_status="ERROR")
    _store_raw(env, logs=RAW_LOGS, error_message=f"Terraform execution failed: key {SECRET_KEY}")

    resp = _get_logs(factory, owner, str(infra.id))

    assert resp.status_code == 200
    assert SECRET_KEY not in resp.data["logs"]
    assert "provider config" not in resp.data["logs"]
    assert SECRET_KEY not in resp.data["error_message"]
    assert resp.data["withheld_lines"] == 1


def test_read_time_drift_emits_a_warning(factory, make_infra_env, caplog):
    owner, infra, env = make_infra_env()
    _store_raw(env, logs=RAW_LOGS)
    caplog.set_level(logging.WARNING, logger=DRIFT_LOGGER)

    _get_logs(factory, owner, str(infra.id))

    drift = _drift_records(caplog)
    assert len(drift) == 1
    assert "logs" in drift[0].getMessage()
    assert str(infra.id) in drift[0].getMessage()
    assert SECRET_KEY not in caplog.text
    assert "provider config" not in caplog.text


def test_clean_row_emits_no_warning(factory, make_infra_env, caplog):
    owner, infra, _env = make_infra_env(logs=redact_provisioning_text(RAW_LOGS).text)
    caplog.set_level(logging.WARNING, logger=DRIFT_LOGGER)

    _get_logs(factory, owner, str(infra.id))

    assert _drift_records(caplog) == []


def test_reports_withheld_and_truncated(factory, make_infra_env, caplog):
    """A clipped log is still a redactor fixed point, so reading one must not look like
    drift. Clipping lands on a line boundary, hence just under the cap rather than on it."""
    stored = _capped_logs(HUGE, "junk 1\njunk 2\njunk 3\n", "Error: THE ACTUAL ERROR")
    assert MAX_LOG_CHARS - 4096 <= len(stored) <= MAX_LOG_CHARS
    owner, infra, _env = make_infra_env(env_status="PROVISIONING", logs=stored)
    caplog.set_level(logging.WARNING, logger=DRIFT_LOGGER)

    resp = _get_logs(factory, owner, str(infra.id))

    assert resp.status_code == 200
    assert resp.data["truncated"] is True
    assert resp.data["withheld_lines"] >= 3
    assert resp.data["logs"].endswith("Error: THE ACTUAL ERROR")
    assert _drift_records(caplog) == []


def test_truncated_raw_row_still_warns(factory, make_infra_env, caplog):
    raw = (PROGRESS_LINE + f"raw provider line {SECRET_KEY}\n") * (MAX_LOG_CHARS // 100)
    owner, infra, env = make_infra_env(env_status="PROVISIONING")
    _store_raw(env, logs=raw[-MAX_LOG_CHARS:])
    caplog.set_level(logging.WARNING, logger=DRIFT_LOGGER)

    resp = _get_logs(factory, owner, str(infra.id))

    assert resp.data["truncated"] is True
    assert SECRET_KEY not in resp.data["logs"]
    assert len(_drift_records(caplog)) == 1
    assert SECRET_KEY not in caplog.text
