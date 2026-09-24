"""Environment.logs is bounded on every write path.

MAX_LOG_CHARS used to be applied inline at each write site and four of the seven sites
forgot it, so a failing apply or destroy could persist an unbounded terraform blob into
Postgres — and from there into every backup and replica. These tests drive each path that
was uncapped with an oversized log and assert the bound holds.
"""

import uuid
from unittest.mock import patch

import pytest
from api.services.terraform_worker import MAX_LOG_CHARS, TerraformWorker, _capped_logs
from django.utils import timezone

CREDENTIALS = {
    "aws_access_key_id": "AKIA_CUSTOMER",
    "aws_secret_access_key": "customer-secret",
    "aws_session_token": "customer-token",
}
TF_VARS = {"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"}

HUGE = "x" * (MAX_LOG_CHARS + 5_000)


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


def _handle_failure(infra, result, retry_count=0):
    TerraformWorker._handle_provision_failure(
        str(infra.id), result, TF_VARS, CREDENTIALS, "us-east-1", "123456789012", retry_count
    )


# ── the helper itself ─────────────────────────────────────────────────────────

def test_capped_logs_clips_to_the_limit():
    assert len(_capped_logs(HUGE)) == MAX_LOG_CHARS


def test_capped_logs_keeps_the_tail():
    """Terraform puts the error at the end — clipping the wrong end throws away the only
    part worth reading."""
    assert _capped_logs("noise" * 200_000 + "THE ACTUAL ERROR").endswith("THE ACTUAL ERROR")


def test_capped_logs_joins_parts_and_tolerates_none():
    assert _capped_logs(None, "a", None, "b") == "ab"


def test_capped_logs_leaves_short_input_untouched():
    assert _capped_logs("[COMMAND] fine") == "[COMMAND] fine"


def test_cap_is_applied_in_exactly_one_place():
    """Guard against a new write site reintroducing an inline slice. The bug was not that
    the cap was wrong, it was that remembering it per-site does not scale."""
    from pathlib import Path

    import api.services.terraform_worker as worker

    source = Path(worker.__file__).read_text()
    assert source.count("[-MAX_LOG_CHARS:]") == 1


# ── the four write paths that were uncapped ───────────────────────────────────

def test_transient_retry_caps_logs(make_infra_env):
    infra, env = make_infra_env()
    with patch("api.services.infra_queue.InfraQueue"), \
            patch.object(TerraformWorker, "_exec_tf",
                         side_effect=AssertionError("retry path must not destroy")):
        _handle_failure(infra, {"error": "Throttling: rate exceeded", "logs": HUGE}, retry_count=0)
    env.refresh_from_db()
    assert len(env.logs) == MAX_LOG_CHARS


def test_rollback_destroy_caps_combined_logs(make_infra_env):
    """Worst case of the four: the apply log and the destroy log are concatenated, so an
    uncapped write here stores roughly twice an already-unbounded blob."""
    infra, env = make_infra_env()
    with patch.object(TerraformWorker, "_exec_tf",
                      return_value={"success": True, "logs": HUGE}):
        _handle_failure(infra, {"error": "AccessDenied", "logs": HUGE})
    env.refresh_from_db()
    assert env.status == "ERROR"
    assert len(env.logs) == MAX_LOG_CHARS


def test_destroy_success_caps_logs(make_infra_env):
    infra, env = make_infra_env(status="DESTROYING", first_activated_at=timezone.now())
    with patch("api.services.terraform_worker.authenticate_infrastructure"), \
            patch.object(TerraformWorker, "_pre_destroy_cleanup"), \
            patch.object(TerraformWorker, "_exec_tf",
                         return_value={"success": True, "logs": HUGE}):
        TerraformWorker.destroy(str(infra.id))
    env.refresh_from_db()
    assert env.status == "DESTROYED"
    assert len(env.logs) == MAX_LOG_CHARS


def test_destroy_failure_caps_logs(make_infra_env):
    infra, env = make_infra_env(status="DESTROYING", first_activated_at=timezone.now())
    with patch("api.services.terraform_worker.authenticate_infrastructure"), \
            patch.object(TerraformWorker, "_pre_destroy_cleanup"), \
            patch.object(TerraformWorker, "_exec_tf",
                         return_value={"success": False, "error": "stuck", "logs": HUGE}):
        TerraformWorker.destroy(str(infra.id))
    env.refresh_from_db()
    assert env.status == "ERROR"
    assert len(env.logs) == MAX_LOG_CHARS


# ── paths that were already capped stay capped ────────────────────────────────

def test_failed_update_on_live_environment_caps_appended_logs(make_infra_env):
    """This site appends to whatever is already stored, so it is the one that grows
    without bound across repeated failures if the cap ever regresses."""
    infra, env = make_infra_env(
        status="UPDATING", first_activated_at=timezone.now(), logs=HUGE
    )
    with patch.object(TerraformWorker, "_exec_tf",
                      side_effect=AssertionError("destroy must not run for a live environment")):
        _handle_failure(infra, {"error": "AccessDenied", "logs": HUGE})
    env.refresh_from_db()
    assert env.status == "ACTIVE"
    assert len(env.logs) == MAX_LOG_CHARS
