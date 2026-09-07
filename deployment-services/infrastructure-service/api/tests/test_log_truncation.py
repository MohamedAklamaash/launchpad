"""Environment.logs is bounded on every write path.

MAX_LOG_CHARS used to be applied inline at each write site and four of the seven sites
forgot it, so a failing apply or destroy could persist an unbounded terraform blob into
Postgres — and from there into every backup and replica. These tests drive each path that
was uncapped with an oversized log and assert the bound holds.
"""

import re
import uuid
from unittest.mock import patch

import pytest
from api.services.log_redaction import redact_provisioning_text
from api.services.terraform_worker import MAX_LOG_CHARS, TerraformWorker, _capped_logs
from django.utils import timezone

CREDENTIALS = {
    "aws_access_key_id": "AKIA_CUSTOMER",
    "aws_secret_access_key": "customer-secret",
    "aws_session_token": "customer-token",
}
TF_VARS = {"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"}

# Allowlist-shaped so it survives write-time redaction: the cap is what's under test here.
PROGRESS_LINE = "module.vpc.aws_vpc.main: Still creating... [10s elapsed]\n"
HUGE = PROGRESS_LINE * (MAX_LOG_CHARS // len(PROGRESS_LINE) + 100)


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


def assert_capped_on_a_line_boundary(logs):
    """The cut lands on a line boundary, so the stored length is at most the cap and
    short of it by less than one line — never exactly the cap, and never degenerate."""
    assert MAX_LOG_CHARS - len(PROGRESS_LINE) < len(logs) <= MAX_LOG_CHARS


# ── the helper itself ─────────────────────────────────────────────────────────

def test_capped_logs_clips_to_the_limit():
    assert_capped_on_a_line_boundary(_capped_logs(HUGE))


def test_capped_logs_is_a_fixed_point_of_the_redactor():
    """The property every write site leans on: `redact(stored) == stored`. The cut is
    placed inside a diagnostic block on purpose — a character-offset cut would leave the
    block's orphaned body as the first stored lines, re-redaction would withhold them,
    and a drift check comparing stored text to its re-redaction would fire on every
    read of every truncated row."""
    head_line = "│ Error: creating ECS Cluster: AccessDeniedException: not authorized"
    block = f"╷\n{head_line}\n│ \n│   with module.ecs.aws_ecs_cluster.main,\n╵\n"
    suffix = PROGRESS_LINE * ((MAX_LOG_CHARS - len(block)) // len(PROGRESS_LINE) + 1)
    stored_uncut = redact_provisioning_text(PROGRESS_LINE * 3 + block + suffix).text
    cut = len(stored_uncut) - MAX_LOG_CHARS - stored_uncut.index(head_line)
    assert 0 < cut < len(head_line), "fixture no longer cuts inside the block's head line"

    capped = _capped_logs(PROGRESS_LINE * 3, block, suffix)
    assert capped.startswith(PROGRESS_LINE.rstrip())
    assert redact_provisioning_text(capped).text == capped


def test_capped_logs_keeps_the_tail():
    """Terraform puts the error at the end — clipping the wrong end throws away the only
    part worth reading."""
    assert _capped_logs(HUGE, "Error: THE ACTUAL ERROR").endswith("Error: THE ACTUAL ERROR")


def test_capped_logs_joins_parts_and_tolerates_none():
    assert _capped_logs(None, "[INIT]", None, "\n[COMMAND]") == "[INIT]\n[COMMAND]"


def test_capped_logs_leaves_short_input_untouched():
    assert _capped_logs("[COMMAND]\nPlan: 1 to add, 0 to change, 0 to destroy.") == \
        "[COMMAND]\nPlan: 1 to add, 0 to change, 0 to destroy."


def test_cap_is_applied_in_exactly_one_place():
    """Guard against a new write site reintroducing an inline slice. The bug was not that
    the cap was wrong, it was that remembering it per-site does not scale."""
    from pathlib import Path

    import api.services.terraform_worker as worker

    source = Path(worker.__file__).read_text()
    assert not re.search(r"\[[^\]]*MAX_LOG_CHARS[^\]]*\]", source), "an inline slice on MAX_LOG_CHARS is back"
    assert source.count("clip_tail(") == 1


# ── the four write paths that were uncapped ───────────────────────────────────

def test_transient_retry_caps_logs(make_infra_env):
    infra, env = make_infra_env()
    with patch("api.services.infra_queue.InfraQueue"), \
            patch.object(TerraformWorker, "_exec_tf",
                         side_effect=AssertionError("retry path must not destroy")):
        _handle_failure(infra, {"error": "Throttling: rate exceeded", "logs": HUGE, "transient": True}, retry_count=0)
    env.refresh_from_db()
    assert_capped_on_a_line_boundary(env.logs)


def test_rollback_destroy_caps_combined_logs(make_infra_env):
    """Worst case of the four: the apply log and the destroy log are concatenated, so an
    uncapped write here stores roughly twice an already-unbounded blob."""
    infra, env = make_infra_env()
    with patch.object(TerraformWorker, "_exec_tf",
                      return_value={"success": True, "logs": HUGE}):
        _handle_failure(infra, {"error": "AccessDenied", "logs": HUGE, "transient": False})
    env.refresh_from_db()
    assert env.status == "ERROR"
    assert_capped_on_a_line_boundary(env.logs)


def test_destroy_success_caps_logs(make_infra_env):
    infra, env = make_infra_env(status="DESTROYING", first_activated_at=timezone.now())
    with patch("api.services.terraform_worker.authenticate_infrastructure"), \
            patch.object(TerraformWorker, "_pre_destroy_cleanup"), \
            patch.object(TerraformWorker, "_exec_tf",
                         return_value={"success": True, "logs": HUGE}):
        TerraformWorker.destroy(str(infra.id))
    env.refresh_from_db()
    assert env.status == "DESTROYED"
    assert_capped_on_a_line_boundary(env.logs)


def test_destroy_failure_caps_logs(make_infra_env):
    infra, env = make_infra_env(status="DESTROYING", first_activated_at=timezone.now())
    with patch("api.services.terraform_worker.authenticate_infrastructure"), \
            patch.object(TerraformWorker, "_pre_destroy_cleanup"), \
            patch.object(TerraformWorker, "_exec_tf",
                         return_value={"success": False, "error": "stuck", "logs": HUGE}):
        TerraformWorker.destroy(str(infra.id))
    env.refresh_from_db()
    assert env.status == "ERROR"
    assert_capped_on_a_line_boundary(env.logs)


# ── paths that were already capped stay capped ────────────────────────────────

def test_failed_update_on_live_environment_caps_appended_logs(make_infra_env):
    """This site appends to whatever is already stored, so it is the one that grows
    without bound across repeated failures if the cap ever regresses."""
    infra, env = make_infra_env(
        status="UPDATING", first_activated_at=timezone.now(), logs=HUGE
    )
    with patch.object(TerraformWorker, "_exec_tf",
                      side_effect=AssertionError("destroy must not run for a live environment")):
        _handle_failure(infra, {"error": "AccessDenied", "logs": HUGE, "transient": False})
    env.refresh_from_db()
    assert env.status == "ACTIVE"
    assert_capped_on_a_line_boundary(env.logs)
