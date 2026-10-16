"""F7 ticket 6/10 — aws/platform_builder.py is the only build path
application_deployment_service.py has (F7 ticket 10 retired the customer-account
CodeBuild fallback this used to be gated behind).

Every deploy builds on platform Fargate capacity, captures a digest from BuildResult,
pins the task definition to it, and transitions through BUILDING -> PUSHING_IMAGE ->
DEPLOYING. Rollback must never call the builder.
"""
import uuid
from unittest.mock import patch

import aws.platform_builder as platform_builder_mod
import pytest
from aws.platform_builder import BuildError, BuildHandle, BuildResult

from api.mock.mock_session import MockSession
from api.models.deployment import Deployment
from api.services.application_deployment_service import ApplicationDeploymentService

ACCOUNT_ID = "123456789012"
SHA = "c" * 40
DIGEST = "sha256:" + "d" * 64


@pytest.fixture(autouse=True)
def _unlimited_build_budget(monkeypatch):
    """F7 ticket 7's caps and their release-on-every-path guarantee are covered by
    test_platform_build_limits.py — these wiring tests are about the build step itself
    and must not depend on Redis being reachable."""
    monkeypatch.setattr(
        "api.services.application_deployment_service.ApplicationDeploymentService"
        "._enforce_build_budgets",
        lambda self, application: None,
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.ApplicationDeploymentService"
        "._release_build_concurrency_slot",
        lambda self, key: None,
    )


@pytest.fixture
def new_app(schema_db):
    """A brand-new ECS app — no target group/service/task definition has ever been
    created for it, matching test_first_deploy_target_group_order.py's fixture shape."""
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


def _fake_handle(application, infrastructure, sha):
    return BuildHandle(
        build_id=f"platform-{uuid.uuid4().hex}",
        infrastructure=infrastructure, application_id=str(application.id),
        repository_name="launchpad-abc", image_tag=f"my-app-{sha}", sha=sha,
        destination=f"...:my-app-{sha}", is_mock=False, task_arn="task-arn-1",
    )


def _fake_result(handle):
    return BuildResult(image_tag=handle.image_tag, resolved_sha=handle.sha, image_digest=DIGEST)


# ── happy path ─────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_platform_build_happy_path_pins_digest_and_sets_pushing_image(
    new_app, mock_service, monkeypatch, settings,
):
    settings.PLATFORM_BASE_DOMAIN = None
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    app, _env = new_app
    service, _session = mock_service

    statuses = []
    errors_at_building = []
    original_save = app.save
    # Real AWS (ticket 9): a new deploy kept showing the previous attempt's error.
    app.error_message = "stale failure from the previous attempt"

    def _save_spy(*args, **kwargs):
        statuses.append(app.status)
        if app.status == "BUILDING":
            errors_at_building.append(app.error_message)
        return original_save(*args, **kwargs)

    monkeypatch.setattr(app, "save", _save_spy)
    monkeypatch.setattr(
        "api.services.application_deployment_service.resolve_commit_sha",
        lambda application, ref=None: SHA,
    )

    handle_holder = {}

    def _fake_start_build(application, infrastructure, sha):
        handle = _fake_handle(application, infrastructure, sha)
        handle_holder["handle"] = handle
        return handle

    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build",
        _fake_start_build,
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.wait_for_build",
        lambda handle: _fake_result(handle),
    )

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        ecs_cls.return_value.create_task_definition.return_value = "arn:task-def:1"
        ecs_cls.return_value.create_service.return_value = (
            f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service"
        )
        url = service.deploy_application(app)

    assert url
    app.refresh_from_db()
    assert app.status == "ACTIVE"
    assert app.build_id == handle_holder["handle"].build_id
    assert app.project_commit_hash == SHA

    image = ecs_cls.return_value.create_task_definition.call_args.kwargs["image"]
    assert image.endswith(f"@{DIGEST}")

    assert "BUILDING" in statuses
    assert "PUSHING_IMAGE" in statuses
    assert statuses.index("BUILDING") < statuses.index("PUSHING_IMAGE")

    deployment = Deployment.objects.get(application=app)
    assert deployment.build_id == handle_holder["handle"].build_id
    assert deployment.image_digest == DIGEST
    assert deployment.commit_sha == SHA

    # F7 ticket 8: build_task_id (the ECS task id behind build_id, used to locate the
    # build's CloudWatch streams) is threaded onto both the Application and the
    # Deployment history row.
    expected_task_id = platform_builder_mod.task_id_from_arn(handle_holder["handle"].task_arn)
    assert app.build_task_id == expected_task_id
    assert deployment.build_task_id == expected_task_id
    assert errors_at_building and all(e is None for e in errors_at_building)


@pytest.mark.django_db

def test_platform_build_uses_the_webhook_sha_as_the_resolve_ref(
    new_app, mock_service, monkeypatch, settings,
):
    """A webhook deploy already set project_commit_hash to a full sha — the platform
    path must pass it as the resolve ref (which, for an already-resolved sha, GitHub
    just confirms) rather than falling back to the branch HEAD. resolve_commit_sha is
    still called in both cases (there's no "skip" branch) — this only exercises which
    ref it's called with."""
    settings.PLATFORM_BASE_DOMAIN = None
    app, _env = new_app
    app.project_commit_hash = SHA
    app.save()
    service, _session = mock_service

    seen_refs = []

    def _resolve_spy(application, ref=None):
        seen_refs.append(ref)
        return SHA

    monkeypatch.setattr(
        "api.services.application_deployment_service.resolve_commit_sha", _resolve_spy,
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build",
        lambda application, infrastructure, sha: _fake_handle(application, infrastructure, sha),
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.wait_for_build",
        lambda handle: _fake_result(handle),
    )

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        ecs_cls.return_value.create_task_definition.return_value = "arn:task-def:1"
        ecs_cls.return_value.create_service.return_value = (
            f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service"
        )
        service.deploy_application(app)

    assert seen_refs == [SHA]


# ── failure path ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_platform_build_failure_is_sanitized_and_recorded(new_app, mock_service, monkeypatch, settings):
    app, _env = new_app
    service, _session = mock_service

    monkeypatch.setattr(
        "api.services.application_deployment_service.resolve_commit_sha",
        lambda application, ref=None: SHA,
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build",
        lambda application, infrastructure, sha: _fake_handle(application, infrastructure, sha),
    )

    def _boom(handle):
        raise BuildError("Build c1a2b3c4 failed: exit code 1\nnpm ERR! missing script: build")

    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.wait_for_build", _boom,
    )

    with pytest.raises(BuildError):
        service.deploy_application(app)

    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "npm ERR! missing script: build" in app.error_message

    deployment = Deployment.objects.get(application=app)
    assert deployment.status == Deployment.STATUS_FAILED
    assert deployment.build_id == app.build_id
    # The platform path resolves the sha before the build even starts — a build that
    # fails during the wait must still record the real commit, not _image_tag's
    # random-suffix placeholder (image_tag is only computed as a pre-build bound for
    # the except block's "was a build attempted" check, never actually built against).
    assert deployment.commit_sha == SHA
    assert deployment.image_tag == f"my-app-{SHA}"
    assert deployment.tag_source == Deployment.TAG_SOURCE_RESOLVED_SHA


# ── no other build path exists ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_deploy_never_constructs_a_codebuild_client(new_app, mock_service, monkeypatch, settings):
    """F7 ticket 10: there is no flag, no fallback, no CodeBuild client anywhere on the
    deploy path — recorded here as a regression guard against reintroducing one, on top
    of the fact that application_deployment_service.py no longer imports the name at
    all (a reintroduction would be a NameError before this test could even run)."""
    settings.PLATFORM_BASE_DOMAIN = None
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    app, _env = new_app
    service, session = mock_service

    monkeypatch.setattr(
        "api.services.application_deployment_service.resolve_commit_sha",
        lambda application, ref=None: SHA,
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build",
        lambda application, infrastructure, sha: _fake_handle(application, infrastructure, sha),
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.wait_for_build",
        lambda handle: _fake_result(handle),
    )

    seen_services = []
    original_client = session.client

    def _spy_client(name, **kwargs):
        seen_services.append(name)
        return original_client(name, **kwargs)

    monkeypatch.setattr(session, "client", _spy_client)

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        ecs_cls.return_value.create_task_definition.return_value = "arn:task-def:1"
        ecs_cls.return_value.create_service.return_value = (
            f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service"
        )
        service.deploy_application(app)

    assert "codebuild" not in seen_services


# ── rollback: never calls the builder ─────────────────────────────────────────────────

@pytest.mark.django_db
def test_rollback_never_calls_the_platform_builder(new_app, mock_service, monkeypatch, settings):
    from api.services.deployment_snapshot import snapshot_env

    settings.PLATFORM_BASE_DOMAIN = None
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    app, _env = new_app
    service, _session = mock_service

    monkeypatch.setattr(
        "api.services.application_deployment_service.resolve_commit_sha",
        lambda application, ref=None: SHA,
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build",
        lambda application, infrastructure, sha: _fake_handle(application, infrastructure, sha),
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.wait_for_build",
        lambda handle: _fake_result(handle),
    )

    # Set up a prior successful deploy to roll back to — this one is allowed to use the
    # (faked) platform builder; only the rollback call below must not.
    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        ecs_cls.return_value.create_task_definition.return_value = "arn:task-def:1"
        ecs_cls.return_value.create_service.return_value = (
            f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service"
        )
        service.deploy_application(app)
    app.refresh_from_db()

    def _boom(*args, **kwargs):
        raise AssertionError("rollback must never call the platform builder")

    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.start_build", _boom,
    )
    monkeypatch.setattr(
        "api.services.application_deployment_service.platform_builder.wait_for_build", _boom,
    )

    keys, env_hash = snapshot_env(app.id, app.envs or {})
    target = Deployment.objects.create(
        application=app, image_tag=f"my-app-{'a' * 40}",
        commit_sha="a" * 40, tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=env_hash, attached_database_ids=[],
        cpu=app.alloted_cpu, memory=app.alloted_memory, port=app.port,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )

    url = service.rollback_application(app, target)

    assert url
    app.refresh_from_db()
    assert app.status == "ACTIVE"


# ── mock/dev mode works end to end, no python-level mocking of the builder itself ─────

@pytest.mark.django_db
def test_mock_builder_end_to_end(new_app, mock_service, monkeypatch, settings, platform_builder_mock_mode):
    """No patching of resolve_commit_sha/start_build/wait_for_build at all — only the
    established is_mock + dev-mode app_config swap (conftest.py's
    platform_builder_mock_mode fixture) so aws/github_source.py's own mock branch runs
    instead of hitting GitHub, proving the real (not test-faked) platform builder mock
    path works end to end."""
    settings.PLATFORM_BASE_DOMAIN = None
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    app, _env = new_app
    service, _session = mock_service

    url = service.deploy_application(app)

    assert url
    app.refresh_from_db()
    assert app.status == "ACTIVE"
    assert app.build_id

    deployment = Deployment.objects.get(application=app)
    assert deployment.build_id == app.build_id
    assert deployment.image_digest and deployment.image_digest.startswith("sha256:")
