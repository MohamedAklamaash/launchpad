"""Hardening guards on the production (non-dev) TerraformWorker paths.

Covers the subprocess env allow-list, the first_activated_at activation stamp, the
previously-activated failure gate that must skip rollback-destroy, and the rule that
raw `terraform output -json` never lands in Environment.logs.
"""
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from api.services.terraform_worker import (
    MAX_RETRIES,
    OUTPUT_FETCH_EXHAUSTED_MESSAGE,
    TerraformWorker,
)
from django.utils import timezone

# authenticate_infrastructure returns the STS credentials now — they are no longer
# persisted on the infrastructure row. A bare MagicMock flows into the redactor as a
# non-string secret and raises inside it, which surfaces as an unrelated ERROR state.
CREDENTIALS = {
    "aws_access_key_id": "AKIA_CUSTOMER",
    "aws_secret_access_key": "customer-secret",
    "aws_session_token": "customer-token",
}
TF_VARS = {"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"}


@pytest.fixture
def make_infra_env(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, status="PROVISIONING", first_activated_at=None, logs=None):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012",
            metadata={"aws_region": "us-east-1"},
        )
        env = Environment.objects.create(
            infrastructure=infra, status=status,
            first_activated_at=first_activated_at, logs=logs,
        )
        return infra, env

    return _make


# ---- #1 terraform subprocess env allow-list ----

def _run_exec_tf():
    completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")
    with patch("api.services.terraform_worker.subprocess.run", return_value=completed) as run:
        TerraformWorker._exec_tf(
            ["terraform", "output", "-json"],
            TF_VARS,
            CREDENTIALS,
            f"env-isolation-{uuid.uuid4()}",
            "us-east-1",
            "123456789012",
            ensure_backend=False,
        )
    return run


@pytest.mark.parametrize("leaked", ["JWT_SECRET", "INTERNAL_API_TOKEN", "DATABASE_PASSWORD"])
def test_exec_tf_env_excludes_platform_secrets(monkeypatch, leaked):
    monkeypatch.setenv(leaked, "platform-only-value")
    run = _run_exec_tf()
    assert run.call_count == 2
    for recorded_call in run.call_args_list:
        assert leaked not in recorded_call.kwargs["env"]


def test_exec_tf_env_passes_path_home_and_customer_credentials(monkeypatch):
    monkeypatch.setenv("PATH", "/sentinel/bin")
    monkeypatch.setenv("HOME", "/sentinel/home")
    env = _run_exec_tf().call_args_list[0].kwargs["env"]
    assert env["PATH"] == "/sentinel/bin"
    assert env["HOME"] == "/sentinel/home"
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA_CUSTOMER"
    assert env["AWS_SECRET_ACCESS_KEY"] == "customer-secret"
    assert env["AWS_SESSION_TOKEN"] == "customer-token"
    assert env["AWS_DEFAULT_REGION"] == "us-east-1"


@pytest.mark.parametrize("missing", ["aws_access_key_id", "aws_secret_access_key", "aws_session_token"])
def test_exec_tf_refuses_to_run_with_missing_credential(missing):
    """A missing credential must fail closed, never fall back to ambient AWS auth."""
    creds = {**CREDENTIALS, missing: ""}
    with patch("api.services.terraform_worker.subprocess.run") as run:
        result = TerraformWorker._exec_tf(
            ["terraform", "apply"], TF_VARS, creds, f"env-{uuid.uuid4()}",
            "us-east-1", "123456789012", ensure_backend=False,
        )
    assert result["success"] is False
    run.assert_not_called()


def test_exec_tf_env_is_exactly_the_allowlist():
    env = _run_exec_tf().call_args_list[0].kwargs["env"]
    assert set(env) == {
        "PATH", "HOME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION", "AWS_EC2_METADATA_DISABLED", "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE", "TF_IN_AUTOMATION", "TF_INPUT", "TF_PLUGIN_CACHE_DIR",
    }


# ---- #5 first_activated_at stamped once ----

def _save_outputs(infra, mock_outputs=None):
    with patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker._save_outputs(
            str(infra.id), {"logs": "[COMMAND] apply ok"}, TF_VARS, CREDENTIALS,
            "us-east-1", "123456789012",
            mock_outputs=mock_outputs if mock_outputs is not None else {"vpc_id": "vpc-0123456789abcdef0"},
        )


def test_first_activation_stamps_first_activated_at(make_infra_env):
    infra, env = make_infra_env()
    _save_outputs(infra)
    env.refresh_from_db()
    assert env.status == "ACTIVE"
    assert env.first_activated_at is not None


def test_reprovision_does_not_overwrite_first_activated_at(make_infra_env):
    original = timezone.now() - timedelta(days=30)
    infra, env = make_infra_env(status="ACTIVE", first_activated_at=original)
    _save_outputs(infra)
    env.refresh_from_db()
    assert env.first_activated_at == original


# ---- #8 terraform output values never persisted to logs ----

def test_save_outputs_logs_key_names_without_values(make_infra_env):
    infra, env = make_infra_env()
    secret = "rds-master-password-do-not-leak"
    payload = json.dumps({
        "vpc_id": {"value": "vpc-0123456789abcdef0"},
        "db_master_password": {"value": secret, "sensitive": True},
    })
    exec_result = {
        "success": True,
        "output": payload,
        "logs": f"[COMMAND]\n{payload}\n",
    }
    with patch.object(TerraformWorker, "_exec_tf", return_value=exec_result), \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker._save_outputs(
            str(infra.id), {"logs": "[COMMAND] apply ok"}, TF_VARS, CREDENTIALS,
            "us-east-1", "123456789012",
        )
    env.refresh_from_db()
    assert secret not in env.logs
    assert "db_master_password" in env.logs
    assert "vpc_id" in env.logs


OUTPUT_FETCH_FAIL = {"success": False, "error": "state lock timeout", "transient": False, "logs": ""}
OUTPUT_FETCH_FAIL_TRANSIENT = {"success": False, "error": "state lock timeout", "transient": True, "logs": ""}
OUTPUT_FETCH_OK = {
    "success": True,
    "output": json.dumps({"vpc_id": {"value": "vpc-0123456789abcdef0"}}),
    "logs": "",
}


def test_save_outputs_retries_output_fetch_then_succeeds(make_infra_env):
    """A single transient output-fetch failure must not cost the provision a success."""
    infra, env = make_infra_env()
    with patch.object(TerraformWorker, "_exec_tf", side_effect=[OUTPUT_FETCH_FAIL, OUTPUT_FETCH_OK]), \
            patch("api.services.terraform_worker.time.sleep") as sleep, \
            patch("api.messaging.producer.producer.infra_producer"):
        saved = TerraformWorker._save_outputs(
            str(infra.id), {"logs": "[COMMAND] apply ok"}, TF_VARS, CREDENTIALS,
            "us-east-1", "123456789012",
        )
    env.refresh_from_db()
    assert saved is True
    assert env.status == "ACTIVE"
    assert env.vpc_id == "vpc-0123456789abcdef0"
    sleep.assert_called_once_with(5)


def test_output_fetch_exhausted_on_activated_environment_stays_active(make_infra_env):
    """Apply already succeeded — real resources exist — so this must never regress to ERROR,
    and must never be reported as a success either: the fixed, always-allowlisted sentence
    lands in error_message instead of the raw (possibly withheld) terraform diagnostic."""
    infra, env = make_infra_env(status="UPDATING", first_activated_at=timezone.now())
    with patch.object(TerraformWorker, "_exec_tf", return_value=OUTPUT_FETCH_FAIL), \
            patch("api.services.terraform_worker.time.sleep") as sleep, \
            patch("api.messaging.producer.producer.infra_producer"):
        saved = TerraformWorker._save_outputs(
            str(infra.id), {"logs": "[COMMAND] apply ok"}, TF_VARS, CREDENTIALS,
            "us-east-1", "123456789012",
        )
    env.refresh_from_db()
    assert saved is False
    assert env.status == "ACTIVE"
    assert env.error_message == OUTPUT_FETCH_EXHAUSTED_MESSAGE
    assert sleep.call_args_list == [call(5), call(10), call(20)]


def test_output_fetch_exhausted_on_first_provision_rolls_back_to_error(make_infra_env):
    """Never-activated infra whose outputs can't be confirmed must be handled exactly like a
    failed `terraform apply` — permanent failure here rolls back and never leaves it looking
    like PROVISIONING is still in flight."""
    infra, env = make_infra_env()
    destroy_ok = {"success": True, "logs": "[COMMAND] destroy ok"}
    with patch.object(
        TerraformWorker, "_exec_tf",
        side_effect=[OUTPUT_FETCH_FAIL, OUTPUT_FETCH_FAIL, OUTPUT_FETCH_FAIL, OUTPUT_FETCH_FAIL, destroy_ok],
    ) as exec_tf, \
            patch("api.services.terraform_worker.time.sleep") as sleep, \
            patch("api.messaging.producer.producer.infra_producer"):
        saved = TerraformWorker._save_outputs(
            str(infra.id), {"logs": "[COMMAND] apply ok"}, TF_VARS, CREDENTIALS,
            "us-east-1", "123456789012",
        )
    env.refresh_from_db()
    assert saved is False
    assert env.status == "ERROR"
    assert env.first_activated_at is None
    assert sleep.call_args_list == [call(5), call(10), call(20)]
    assert exec_tf.call_args_list[-1].args[0][:2] == ["terraform", "destroy"]


def test_output_fetch_exhausted_transient_on_first_provision_requeues(make_infra_env):
    """A transient output-read failure on a first provision gets the same bounded requeue
    a transient apply failure gets — never a silent PROVISIONING with nothing driving it."""
    infra, env = make_infra_env()
    with patch.object(TerraformWorker, "_exec_tf", return_value=OUTPUT_FETCH_FAIL_TRANSIENT), \
            patch("api.services.terraform_worker.time.sleep"), \
            patch("api.services.infra_queue.InfraQueue") as Q, \
            patch("api.messaging.producer.producer.infra_producer"):
        saved = TerraformWorker._save_outputs(
            str(infra.id), {"logs": "[COMMAND] apply ok"}, TF_VARS, CREDENTIALS,
            "us-east-1", "123456789012",
        )
    Q.enqueue_provision.assert_called_once_with(str(infra.id))
    env.refresh_from_db()
    assert saved is False
    assert env.status == "PROVISIONING"


def test_provision_recovers_from_output_fetch_hiccup_on_first_apply(make_infra_env):
    """End-to-end: a first apply whose output read fails once then succeeds must still
    activate normally, not get stuck or reported as anything other than success."""
    infra, env = make_infra_env(status="PENDING")
    apply_ok = {"success": True, "logs": "[COMMAND] apply ok"}
    with patch("api.services.terraform_worker.authenticate_infrastructure",
                  return_value=dict(CREDENTIALS)), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=[apply_ok, OUTPUT_FETCH_FAIL, OUTPUT_FETCH_OK]), \
            patch("api.services.terraform_worker.time.sleep") as sleep, \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra.id))
    env.refresh_from_db()
    assert env.status == "ACTIVE"
    assert env.first_activated_at is not None
    assert env.vpc_id == "vpc-0123456789abcdef0"
    sleep.assert_called_once_with(5)


# ---- #6 provision-failure gate on a previously-activated environment ----

FAILURE = {"error": "AccessDenied: not authorized", "logs": "[COMMAND] apply exploded", "transient": False}
TRANSIENT_FAILURE = {"error": "Throttling: rate exceeded", "logs": "[COMMAND] throttled", "transient": True}


def _handle_failure(infra, result, retry_count=0):
    TerraformWorker._handle_provision_failure(
        str(infra.id), result, TF_VARS, CREDENTIALS, "us-east-1", "123456789012", retry_count
    )


def test_failure_on_activated_environment_skips_destroy_and_restores_active(make_infra_env):
    infra, env = make_infra_env(
        status="UPDATING", first_activated_at=timezone.now(),
        logs="[COMMAND]\nmodule.vpc.aws_vpc.main: Creating...",
    )
    with patch.object(TerraformWorker, "_exec_tf",
                      side_effect=AssertionError("destroy must not run for a live environment")):
        _handle_failure(infra, FAILURE)
    env.refresh_from_db()
    assert env.status == "ACTIVE"
    assert "[FAILED UPDATE]" in env.logs
    assert "module.vpc.aws_vpc.main: Creating..." in env.logs
    assert "restored to ACTIVE" in env.error_message


def test_transient_failure_on_activated_environment_still_retries(make_infra_env):
    """Retrying never destroys anything, so a live env's transient errors must still retry."""
    infra, env = make_infra_env(status="UPDATING", first_activated_at=timezone.now())
    with patch("api.services.infra_queue.InfraQueue") as Q, \
            patch.object(TerraformWorker, "_exec_tf",
                         side_effect=AssertionError("retry path must not destroy")):
        _handle_failure(infra, TRANSIENT_FAILURE, retry_count=0)
    Q.enqueue_provision.assert_called_once_with(str(infra.id))
    env.refresh_from_db()
    assert env.status == "UPDATING"


def test_transient_failure_on_never_activated_environment_still_retries(make_infra_env):
    infra, env = make_infra_env()
    with patch("api.services.infra_queue.InfraQueue") as Q, \
            patch.object(TerraformWorker, "_exec_tf",
                         side_effect=AssertionError("retry path must not destroy")):
        _handle_failure(infra, TRANSIENT_FAILURE, retry_count=0)
    Q.enqueue_provision.assert_called_once_with(str(infra.id))
    env.refresh_from_db()
    assert env.status == "PROVISIONING"


def test_permanent_failure_on_never_activated_environment_still_rolls_back(make_infra_env):
    infra, env = make_infra_env()
    with patch.object(TerraformWorker, "_exec_tf",
                      return_value={"success": True, "logs": "[COMMAND] destroy ok"}) as exec_tf:
        _handle_failure(infra, FAILURE)
    assert exec_tf.call_count == 1
    assert exec_tf.call_args.args[0][:2] == ["terraform", "destroy"]
    env.refresh_from_db()
    assert env.status == "ERROR"


def test_exhausted_transient_retries_on_never_activated_environment_rolls_back(make_infra_env):
    infra, env = make_infra_env()
    with patch("api.services.infra_queue.InfraQueue") as Q, \
            patch.object(TerraformWorker, "_exec_tf",
                         return_value={"success": True, "logs": "[COMMAND] destroy ok"}):
        _handle_failure(infra, TRANSIENT_FAILURE, retry_count=MAX_RETRIES)
    Q.enqueue_provision.assert_not_called()
    env.refresh_from_db()
    assert env.status == "ERROR"
