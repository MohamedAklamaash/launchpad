"""Redaction is applied at the worker boundary, not left to each write site.

Two live leaks closed here: `error_message` used to receive raw terraform stderr, and the
worker's generic exception handler stored `str(e)` — for a boto3 ClientError from
AssumeRole that string carries the platform's own account id and IAM user ARN.
"""

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from api.services.log_redaction import redact_provisioning_text
from api.services.terraform_worker import (
    MAX_ERROR_CHARS,
    TerraformWorker,
    _capped_error,
)
from botocore.exceptions import ClientError
from django.utils import timezone

# Assembled from parts rather than written as literals. These have to match the exact
# AWS key and secret shapes, because those shapes are what the redactor keys on — and a
# literal of the exact shape is, to a secret scanner, indistinguishable from a real
# credential. api/tests/test_mode.py sidesteps the same detector by using a deliberately
# over-length key; this suite cannot, since the length is the thing under test.
CREDENTIALS = {
    "aws_access_key_id": "ASIA" + "NOTAREALKEY00000",
    "aws_secret_access_key": ("notarealsecret" * 3)[:40],
    "aws_session_token": "bm90YXJlYWxzZXNzaW9udG9rZW4=",
}
TF_VARS = {"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"}
PROGRESS_LINE = "module.vpc.aws_vpc.main: Still creating... [10s elapsed]\n"
INIT_OK = SimpleNamespace(returncode=0, stdout="Terraform has been successfully initialized!", stderr="")


def _failed(stderr):
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


def _assume_role_denied():
    return ClientError(
        {"Error": {"Code": "AccessDenied",
                   "Message": "User: arn:aws:iam::221082203366:user/aklamaash-terraform is not "
                              "authorized to perform: sts:AssumeRole"}},
        "AssumeRole",
    )


@pytest.fixture
def make_infra_env(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, status="PROVISIONING", first_activated_at=None):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012",
            metadata={"aws_region": "us-east-1", **CREDENTIALS},
        )
        env = Environment.objects.create(
            infrastructure=infra, status=status, first_activated_at=first_activated_at,
        )
        return infra, env

    return _make


def _exec_tf_with(*runs):
    with patch("api.services.terraform_worker.subprocess.run", side_effect=list(runs)):
        return TerraformWorker._exec_tf(
            ["terraform", "apply", "-auto-approve", "-no-color", "-input=false"],
            TF_VARS, CREDENTIALS, f"env-{uuid.uuid4()}", "us-east-1", "123456789012",
            ensure_backend=False,
        )


def _handle_failure(infra, result, retry_count=0):
    TerraformWorker._handle_provision_failure(
        str(infra.id), result, TF_VARS, CREDENTIALS, "us-east-1", "123456789012", retry_count
    )


# ── _exec_tf never returns raw terraform text ─────────────────────────────────

def test_exec_tf_scrubs_credential_values_from_logs_and_error():
    stderr = "Error: configuring provider: " + " ".join(CREDENTIALS.values()) + "\n"
    result = _exec_tf_with(INIT_OK, _failed(stderr))
    assert result["success"] is False
    for value in CREDENTIALS.values():
        assert value not in result["logs"]
        assert value not in result["error"]
    assert "<redacted>" in result["error"]
    assert "[INIT]" in result["logs"] and "[COMMAND]" in result["logs"]


def test_exec_tf_returns_transient_flag_computed_before_redaction():
    result = _exec_tf_with(INIT_OK, _failed("Throttling: rate exceeded\n"))
    assert result["transient"] is True
    assert "Throttling" not in result["error"]


def test_exec_tf_success_keeps_output_raw_for_json_parsing():
    payload = '{"vpc_id": {"value": "vpc-0abc"}}'
    result = _exec_tf_with(INIT_OK, SimpleNamespace(returncode=0, stdout=payload, stderr=""))
    assert result["success"] is True
    assert result["transient"] is False
    assert result["output"] == payload
    assert payload not in result["logs"]


def test_transient_retry_still_fires_after_redaction(make_infra_env):
    infra, env = make_infra_env()
    with patch("api.services.terraform_worker.authenticate_infrastructure"), \
            patch.object(TerraformWorker, "_ensure_backend", return_value=("bucket", "table")), \
            patch("api.services.terraform_worker.subprocess.run",
                  side_effect=[INIT_OK, _failed("Throttling: rate exceeded\n")]), \
            patch("api.services.infra_queue.InfraQueue") as Q:
        TerraformWorker.provision(str(infra.id))
    Q.enqueue_provision.assert_called_once_with(str(infra.id))
    env.refresh_from_db()
    assert env.error_message.startswith("Retry 1:")


# ── the live leaks ────────────────────────────────────────────────────────────

def test_database_error_message_is_redacted(make_infra_env):
    from api.models.database import Database

    infra, env = make_infra_env(status="UPDATING", first_activated_at=timezone.now())
    row = Database.objects.create(
        environment=env, name="cache", engine="redis", engine_version="7.1",
        instance_class="cache.t3.micro", allocated_storage=0, status="PENDING",
    )
    secret = CREDENTIALS["aws_access_key_id"]
    with patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("must not destroy")):
        _handle_failure(infra, {"error": f"Error: bad credentials {secret}\n", "logs": "", "transient": False})
    env.refresh_from_db()
    row.refresh_from_db()
    assert secret not in env.error_message
    assert secret not in row.error_message
    assert env.error_message.startswith("Update failed; environment restored to ACTIVE:")
    assert row.error_message.startswith("Update failed:")
    assert "<redacted>" in row.error_message


def test_error_message_is_capped_and_keeps_cleanup_suffix(make_infra_env):
    infra, env = make_infra_env()
    huge_error = PROGRESS_LINE * (MAX_ERROR_CHARS // len(PROGRESS_LINE) + 50)
    destroy_failed = {"success": False, "error": "Error: destroy stuck on VPC\n", "logs": "", "transient": False}
    with patch.object(TerraformWorker, "_exec_tf", return_value=destroy_failed):
        _handle_failure(infra, {"error": huge_error, "logs": "", "transient": False})
    env.refresh_from_db()
    assert env.status == "ERROR"
    assert len(env.error_message) <= 2 * MAX_ERROR_CHARS + 1
    assert "Cleanup:" in env.error_message
    assert "WARNING: Cleanup failed. Manual cleanup required in AWS account." in env.error_message
    assert env.error_message.endswith("Error: destroy stuck on VPC")


def test_capped_error_is_a_fixed_point_of_the_redactor():
    """Same property as `test_capped_logs_is_a_fixed_point_of_the_redactor`, for the
    head clip: the cut lands inside one of many small diagnostic blocks, and the
    trailing partial line is dropped rather than stored."""
    block = ("╷\n│ Error: creating ECS Cluster: AccessDeniedException: not authorized\n"
             "│   with module.ecs.aws_ecs_cluster.main,\n╵\n")
    huge = block * (MAX_ERROR_CHARS // len(block) + 5)
    capped = _capped_error(huge)
    assert len(capped) <= MAX_ERROR_CHARS
    assert len(capped) > MAX_ERROR_CHARS - len(block)
    assert redact_provisioning_text(capped).text == capped


@pytest.mark.parametrize("first_activated_at", [None, timezone.now()], ids=["first-provision", "update"])
def test_generic_exception_error_message_reduces_client_error(make_infra_env, first_activated_at):
    infra, env = make_infra_env(status="PROVISIONING", first_activated_at=first_activated_at)
    with patch("api.services.terraform_worker.authenticate_infrastructure", side_effect=_assume_role_denied()):
        TerraformWorker.provision(str(infra.id))
    env.refresh_from_db()
    assert "221082203366" not in env.error_message
    assert "aklamaash-terraform" not in env.error_message
    assert env.error_message.endswith("An error occurred (AccessDenied) when calling the AssumeRole operation")


def test_run_worker_never_emails_a_raw_exception_string():
    """`run_provision`/`run_destroy` are closures inside the management command and
    cannot be driven in isolation; hold the invariant at the source instead, the same
    way `test_cap_is_applied_in_exactly_one_place` does."""
    from api.management.commands import run_worker

    source = Path(run_worker.__file__).read_text()
    assert ", str(e))" not in source
    assert 'f"{e}"' not in source
    assert "repr(e)" not in source
    assert source.count("_capped_error(str(e))") == 2
    assert "error_message=_capped_error(park_message)" in source
