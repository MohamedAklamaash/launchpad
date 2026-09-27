"""ApplicationDeploymentService.backfill_host_routing — migrating an already-deployed ECS
app into host mode once its infra becomes TLS-ready, without a new code push (F1b part 3a).
"""
import uuid
from typing import ClassVar
from unittest.mock import patch

import pytest
from shared.enums.orchestrator import ComputeType


@pytest.fixture
def backfill_fixtures(schema_db, settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    from api.mock.mock_session import MockSession
    from api.models.application import Application
    from api.models.deployment import Deployment
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User
    from api.services.application_deployment_service import ApplicationDeploymentService

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512, is_mock=True, code="123456789012",
        dns_label="0123456789abcdef", tls_status="ISSUED", dns_synced=True, https_ready=True,
    )
    env = Environment.objects.create(
        id=uuid.uuid4(), infrastructure=infra, status='ACTIVE',
        ecr_repository_url="123456789012.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc",
        ecs_task_execution_role_arn="arn:aws:iam::123456789012:role/exec",
        alb_arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/x/1",
        cluster_arn="arn:aws:ecs:us-east-1:123456789012:cluster/x",
    )
    app = Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
        status='ACTIVE', task_definition_arn="arn:aws:ecs:us-east-1:123456789012:task-definition/my-app-task:1",
        service_arn="arn:aws:ecs:us-east-1:123456789012:service/my-app-service",
        target_group_arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/my-app/abc",
    )
    Deployment.objects.create(
        application=app, image_tag="my-app-abc123", image_digest="sha256:" + "a" * 64,
        commit_sha="a" * 40, tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA,
        compute_type=ComputeType.ECS_FARGATE, env_keys=[], env_values_hash="x",
        attached_database_ids=[], cpu=256, memory=512, port=8080,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )
    service = ApplicationDeploymentService()
    session = MockSession(region="us-east-1", account_id="123456789012", infra_id=str(infra.id))
    with patch.object(service, "_create_aws_session", return_value=session):
        yield service, app, env, infra


@pytest.mark.django_db
def test_backfill_migrates_an_eligible_app(backfill_fixtures):
    service, app, _env, _infra = backfill_fixtures

    migrated, reason = service.backfill_host_routing(app)

    assert (migrated, reason) == (True, "migrated")
    app.refresh_from_db()
    assert app.host_forward_rule_arn is not None


@pytest.mark.django_db
def test_backfill_is_idempotent(backfill_fixtures):
    service, app, _env, _infra = backfill_fixtures

    service.backfill_host_routing(app)
    migrated, reason = service.backfill_host_routing(app)

    assert (migrated, reason) == (False, "already_host_mode")


@pytest.mark.django_db
def test_backfill_skips_when_infra_not_tls_ready(backfill_fixtures):
    service, app, _env, infra = backfill_fixtures
    infra.tls_status = "PENDING"
    infra.save()

    migrated, reason = service.backfill_host_routing(app)

    assert migrated is False
    assert reason == "tls_not_issued"
    app.refresh_from_db()
    assert app.host_forward_rule_arn is None


@pytest.mark.django_db
def test_backfill_skips_eks_apps(backfill_fixtures):
    service, app, _env, infra = backfill_fixtures
    infra.compute_type = ComputeType.EKS
    infra.save()
    app.infrastructure = infra

    migrated, reason = service.backfill_host_routing(app)

    assert (migrated, reason) == (False, "eks_not_applicable")


@pytest.mark.django_db
def test_backfill_skips_apps_not_active(backfill_fixtures):
    service, app, _env, _infra = backfill_fixtures
    app.status = 'FAILED'
    app.save()

    migrated, reason = service.backfill_host_routing(app)

    assert migrated is False
    assert reason == "status_failed"


@pytest.mark.django_db
def test_backfill_skips_without_deployment_history(backfill_fixtures):
    from api.models.deployment import Deployment

    service, app, _env, _infra = backfill_fixtures
    Deployment.objects.filter(application=app).delete()

    migrated, reason = service.backfill_host_routing(app)

    assert (migrated, reason) == (False, "no_deployment_history")


# ── RECOMMENDED item 4: rollback if _configure_host_routing fails after the ECS service ──
# ── has already been moved to the new task definition ────────────────────────────────────

@pytest.mark.django_db
def test_backfill_rolls_back_the_ecs_service_if_configure_host_routing_fails(backfill_fixtures):
    service, app, _env, _infra = backfill_fixtures
    original_task_def = app.task_definition_arn

    with patch.object(service, "_configure_host_routing", side_effect=RuntimeError("ALB API down")):
        migrated, reason = service.backfill_host_routing(app)

    assert (migrated, reason) == (False, "failed_rolled_back")
    app.refresh_from_db()
    assert app.task_definition_arn == original_task_def
    assert app.host_forward_rule_arn is None


@pytest.mark.django_db
def test_backfill_rollback_failure_is_logged_not_raised(backfill_fixtures):
    """Even if the rollback's own update_service call fails, backfill_host_routing must
    still return cleanly rather than propagating — the original failure is already the
    actionable signal."""
    from api.mock.mock_session import MockClient

    service, app, _env, _infra = backfill_fixtures
    # First update_service call is the migration's own (must succeed); the second is the
    # rollback's, which this test forces to fail.
    with patch.object(service, "_configure_host_routing", side_effect=RuntimeError("ALB API down")), \
            patch.object(MockClient, "update_service", side_effect=[
                {"service": {"serviceArn": "arn:x", "serviceName": "my-app-service"}},
                RuntimeError("ECS unavailable"),
            ]):
        migrated, reason = service.backfill_host_routing(app)

    assert (migrated, reason) == (False, "failed_rolled_back")


@pytest.mark.django_db
def test_evaluate_backfill_eligibility_is_read_only_and_matches_backfill_host_routing(backfill_fixtures):
    service, app, _env, _infra = backfill_fixtures

    eligible, reason, ctx = service.evaluate_backfill_eligibility(app)

    assert eligible is True
    assert reason == "eligible"
    assert ctx["app_hostname"] == "my-app.0123456789abcdef.launchpad.aklamaash.me"
    # No mutation happened — the app is unchanged.
    app.refresh_from_db()
    assert app.host_forward_rule_arn is None
    assert app.task_definition_arn == "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app-task:1"


@pytest.mark.django_db
def test_evaluate_backfill_eligibility_reports_the_same_skip_reason(backfill_fixtures):
    service, app, _env, infra = backfill_fixtures
    infra.tls_status = "PENDING"
    infra.save()

    eligible, reason, ctx = service.evaluate_backfill_eligibility(app)

    assert (eligible, reason, ctx) == (False, "tls_not_issued", None)


# ── management command: lock + real dry-run ──────────────────────────────────────────────

class _InMemoryDeploymentLock:
    """Stands in for the Redis-backed DeploymentLock so these tests need no Redis server.
    Holders are shared across instances, like keys in a real Redis."""

    _holders: ClassVar[dict] = {}

    def acquire(self, app_id, worker_id):
        if app_id in self._holders:
            return False
        self._holders[app_id] = worker_id
        return True

    def release(self, app_id, worker_id):
        if self._holders.get(app_id) == worker_id:
            del self._holders[app_id]

    def is_locked(self, app_id):
        return app_id in self._holders


@pytest.fixture
def command_session_patch(backfill_fixtures, monkeypatch):
    """The management command constructs its own ApplicationDeploymentService instance,
    so the fixture's per-instance _create_aws_session patch doesn't reach it — patch at
    the class level instead for these command-level tests."""
    from api.services import deployment_lock
    from api.services.application_deployment_service import ApplicationDeploymentService

    _InMemoryDeploymentLock._holders = {}
    monkeypatch.setattr(deployment_lock, "DeploymentLock", _InMemoryDeploymentLock)
    service, app, env, infra = backfill_fixtures
    with patch.object(ApplicationDeploymentService, "_create_aws_session", return_value=service._create_aws_session(infra)):
        yield app, env, infra


@pytest.mark.django_db
def test_command_dry_run_evaluates_eligibility_without_mutating(command_session_patch):
    from io import StringIO

    from django.core.management import call_command

    app, _env, _infra = command_session_patch
    out = StringIO()

    call_command("backfill_host_routing", "--dry-run", stdout=out)

    assert f"would migrate {app.id}" in out.getvalue()
    app.refresh_from_db()
    assert app.host_forward_rule_arn is None


@pytest.mark.django_db
def test_command_dry_run_reports_ineligible_apps_with_their_reason(command_session_patch):
    from io import StringIO

    from django.core.management import call_command

    app, _env, infra = command_session_patch
    infra.tls_status = "PENDING"
    infra.save()
    out = StringIO()

    call_command("backfill_host_routing", "--dry-run", stdout=out)

    assert f"would skip {app.id} ({app.name}): tls_not_issued" in out.getvalue()


@pytest.mark.django_db
def test_command_skips_an_app_locked_by_a_concurrent_deploy(command_session_patch):
    from io import StringIO

    from django.core.management import call_command

    from api.services.deployment_lock import DeploymentLock

    app, _env, _infra = command_session_patch
    lock = DeploymentLock()
    assert lock.acquire(str(app.id), "some-other-deploy-worker")
    out = StringIO()

    try:
        call_command("backfill_host_routing", stdout=out)
    finally:
        lock.release(str(app.id), "some-other-deploy-worker")

    assert "locked=1" in out.getvalue()
    app.refresh_from_db()
    assert app.host_forward_rule_arn is None


@pytest.mark.django_db
def test_command_migrates_and_releases_the_lock(command_session_patch):
    from io import StringIO

    from django.core.management import call_command

    from api.services.deployment_lock import DeploymentLock

    app, _env, _infra = command_session_patch
    out = StringIO()

    call_command("backfill_host_routing", stdout=out)

    assert "migrated=1" in out.getvalue()
    app.refresh_from_db()
    assert app.host_forward_rule_arn is not None
    assert DeploymentLock().is_locked(str(app.id)) is False
