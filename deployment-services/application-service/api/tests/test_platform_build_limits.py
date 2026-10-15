"""F7 ticket 7 — abuse limits on the platform build path.

The Redis-backed primitives themselves (acquire_concurrency_slot/release_concurrency_slot
fixed-window math, fail-closed on Redis down) are covered in
infrastructure-service/api/tests/test_rate_budget.py, alongside customer_call_budget —
one shared module, one test file. These tests cover the wiring:
ApplicationDeploymentService._enforce_build_budgets/_release_build_concurrency_slot
actually gate and release around the platform build step of a real deploy_application
call, on every exit path.
"""
import uuid
from unittest.mock import patch

import pytest
from aws.platform_builder import BuildError, BuildHandle, BuildResult
from shared.errors.exception import HttpError

from api.mock.mock_session import MockSession
from api.services.application_deployment_service import (
    ApplicationDeploymentService,
    BuildLimitExceeded,
)

ACCOUNT_ID = "123456789012"
SHA = "e" * 40
DIGEST = "sha256:" + "f" * 64


@pytest.fixture
def new_app(schema_db):
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=2048, is_mock=True, code=ACCOUNT_ID,
        is_cloud_authenticated=True,
    )
    env = Environment.objects.create(
        infrastructure=infra, status="ACTIVE", vpc_id="vpc-1",
        cluster_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:cluster/c",
        alb_arn=f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:loadbalancer/app/a/1",
        alb_dns="alb.example.com", alb_security_group_id="sg-alb",
        ecr_repository_url=f"{ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc",
        ecs_task_execution_role_arn=f"arn:aws:iam::{ACCOUNT_ID}:role/exec",
    )
    app = Application.objects.create(
        user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="a" * 40, envs={}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    return app, env


@pytest.fixture
def mock_service(monkeypatch):
    service = ApplicationDeploymentService()
    session = MockSession(region="us-east-1", account_id=ACCOUNT_ID)
    monkeypatch.setattr(service, "_create_aws_session", lambda _infra: session)
    return service, session


@pytest.fixture(autouse=True)
def _platform_build_on(settings, monkeypatch):
    settings.PLATFORM_BUILD_ENABLED = True
    settings.PLATFORM_BASE_DOMAIN = None
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    monkeypatch.setattr(
        "api.services.application_deployment_service.resolve_commit_sha",
        lambda application, ref=None: SHA,
    )


def _fake_handle(application, infrastructure, sha):
    return BuildHandle(
        build_id=f"platform-{uuid.uuid4().hex}",
        infrastructure=infrastructure, application_id=str(application.id),
        repository_name="launchpad-abc", image_tag=f"my-app-{sha}", sha=sha,
        destination=f"...:my-app-{sha}", is_mock=False, task_arn="task-arn-1",
    )


def _fake_result(handle):
    return BuildResult(image_tag=handle.image_tag, resolved_sha=handle.sha, image_digest=DIGEST)


def _patch_builder(monkeypatch, *, wait_raises=None):
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build",
        lambda application, infrastructure, sha: _fake_handle(application, infrastructure, sha),
    )
    if wait_raises is not None:
        def _raise(handle):
            raise wait_raises
        monkeypatch.setattr(
            "api.services.application_deployment_service.platform_builder.wait_for_build", _raise,
        )
    else:
        monkeypatch.setattr(
            "api.services.application_deployment_service.platform_builder.wait_for_build",
            lambda handle: _fake_result(handle),
        )


def _patch_ecs(ecs_cls):
    ecs_cls.return_value.create_task_definition.return_value = "arn:task-def:1"
    ecs_cls.return_value.create_service.return_value = (
        f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service"
    )


# ── caps enforced ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_rate_cap_blocks_the_build_and_never_starts_it(new_app, mock_service, monkeypatch, settings):
    settings.PLATFORM_BUILD_MAX_PER_USER_PER_HOUR = 1
    app, _env = new_app
    service, _session = mock_service

    def _boom(*args, **kwargs):
        raise AssertionError("must never start a build once the rate cap is hit")

    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build", _boom,
    )
    monkeypatch.setattr(
        "shared.ratelimit.budget.customer_call_budget",
        lambda user_id, bucket, limit, window: 42,  # already over budget
    )

    with pytest.raises(BuildLimitExceeded, match="rate limit"):
        service.deploy_application(app)

    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "rate limit" in app.error_message


@pytest.mark.django_db
def test_concurrency_cap_blocks_the_build_and_never_starts_it(new_app, mock_service, monkeypatch, settings):
    settings.PLATFORM_BUILD_MAX_CONCURRENT_PER_INFRA = 2
    app, _env = new_app
    service, _session = mock_service

    def _boom(*args, **kwargs):
        raise AssertionError("must never start a build once the concurrency cap is hit")

    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build", _boom,
    )
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)
    monkeypatch.setattr("shared.ratelimit.budget.acquire_concurrency_slot", lambda *a, **k: False)

    with pytest.raises(BuildLimitExceeded, match="oncurrent build"):
        service.deploy_application(app)

    app.refresh_from_db()
    assert app.status == "FAILED"


# ── release on every exit path ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_slot_released_on_success(new_app, mock_service, monkeypatch, settings):
    app, _env = new_app
    service, _session = mock_service
    _patch_builder(monkeypatch)
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)
    monkeypatch.setattr("shared.ratelimit.budget.acquire_concurrency_slot", lambda *a, **k: True)
    released = []
    monkeypatch.setattr(
        "shared.ratelimit.budget.release_concurrency_slot", lambda key: released.append(key),
    )

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        _patch_ecs(ecs_cls)
        service.deploy_application(app)

    assert released == [f"builds_concurrent:{app.infrastructure_id}"]


@pytest.mark.django_db
def test_slot_released_when_the_build_fails(new_app, mock_service, monkeypatch, settings):
    app, _env = new_app
    service, _session = mock_service
    _patch_builder(monkeypatch, wait_raises=BuildError("Build failed: exit code 1"))
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)
    monkeypatch.setattr("shared.ratelimit.budget.acquire_concurrency_slot", lambda *a, **k: True)
    released = []
    monkeypatch.setattr(
        "shared.ratelimit.budget.release_concurrency_slot", lambda key: released.append(key),
    )

    with pytest.raises(BuildError):
        service.deploy_application(app)

    assert released == [f"builds_concurrent:{app.infrastructure_id}"]


@pytest.mark.django_db
def test_slot_released_when_the_build_times_out(new_app, mock_service, monkeypatch, settings):
    """platform_builder.wait_for_build raises BuildError on a force-stopped timeout —
    same exception type as any other build failure from this service's point of view."""
    app, _env = new_app
    service, _session = mock_service
    _patch_builder(monkeypatch, wait_raises=BuildError("Build timed out after 1800 seconds"))
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)
    monkeypatch.setattr("shared.ratelimit.budget.acquire_concurrency_slot", lambda *a, **k: True)
    released = []
    monkeypatch.setattr(
        "shared.ratelimit.budget.release_concurrency_slot", lambda key: released.append(key),
    )

    with pytest.raises(BuildError, match="timed out"):
        service.deploy_application(app)

    assert released == [f"builds_concurrent:{app.infrastructure_id}"]


@pytest.mark.django_db
def test_slot_released_on_an_unrelated_exception_mid_build(new_app, mock_service, monkeypatch, settings):
    app, _env = new_app
    service, _session = mock_service
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build",
        lambda application, infrastructure, sha: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)
    monkeypatch.setattr("shared.ratelimit.budget.acquire_concurrency_slot", lambda *a, **k: True)
    released = []
    monkeypatch.setattr(
        "shared.ratelimit.budget.release_concurrency_slot", lambda key: released.append(key),
    )

    with pytest.raises(RuntimeError):
        service.deploy_application(app)

    assert released == [f"builds_concurrent:{app.infrastructure_id}"]


# ── Redis down fails closed ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_redis_down_on_rate_check_fails_closed(new_app, mock_service, monkeypatch, settings):
    app, _env = new_app
    service, _session = mock_service

    def _boom(*args, **kwargs):
        raise AssertionError("must never start a build when the limiter is unavailable")

    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build", _boom,
    )
    monkeypatch.setattr(
        "shared.ratelimit.budget.customer_call_budget",
        lambda *a, **k: (_ for _ in ()).throw(HttpError(message="down", status_code=503)),
    )

    with pytest.raises(ValueError, match="unavailable") as excinfo:
        service.deploy_application(app)
    # A limiter outage may be transient, so it stays retryable (not BuildLimitExceeded).
    assert not isinstance(excinfo.value, BuildLimitExceeded)

    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "unavailable" in app.error_message


@pytest.mark.django_db
def test_redis_down_on_concurrency_check_fails_closed(new_app, mock_service, monkeypatch, settings):
    app, _env = new_app
    service, _session = mock_service

    def _boom(*args, **kwargs):
        raise AssertionError("must never start a build when the limiter is unavailable")

    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build", _boom,
    )
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)
    monkeypatch.setattr(
        "shared.ratelimit.budget.acquire_concurrency_slot",
        lambda *a, **k: (_ for _ in ()).throw(HttpError(message="down", status_code=503)),
    )

    with pytest.raises(ValueError, match="unavailable") as excinfo:
        service.deploy_application(app)
    # A limiter outage may be transient, so it stays retryable (not BuildLimitExceeded).
    assert not isinstance(excinfo.value, BuildLimitExceeded)

    app.refresh_from_db()
    assert app.status == "FAILED"


def test_worker_acks_a_build_limit_refusal_instead_of_retrying():
    """A refusal is recorded once; retrying would charge the budget again and write
    another FAILED row per attempt."""
    import inspect

    from api.management.commands import run_worker

    source = inspect.getsource(run_worker)
    limit_branch = source.index("except BuildLimitExceeded")
    generic_branch = source.index("except Exception:", limit_branch - 400)
    assert limit_branch < generic_branch
    assert "DeploymentQueue.ack_job(job)" in source[limit_branch:generic_branch]

