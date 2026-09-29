"""H2 (hardening): once an infrastructure's read-model mirror shows exited_at set,
application-service refuses the mutating/deploying actions listed in plan/H-hardening.md —
app create/update, deploy, retry, rollback, resume-auto-deploy, GitHub webhook deploys,
custom-domain attach, sleep/wake (security review RECOMMENDED 1), and
backfill_host_routing — and the deploy worker re-checks at dequeue time so a job queued
before exit cannot run after. Runtime logs and app delete are deliberately NOT gated here
— see plan/H-hardening.md Decisions."""
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory

ACCOUNT_ID = "123456789012"


def _req(user_id):
    return SimpleNamespace(user=SimpleNamespace(id=user_id))


@pytest.fixture
def exited_app(schema_db):
    """An ACTIVE app on an infra whose read-model mirror already shows it exited."""
    from django.utils import timezone

    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=2048, is_mock=True, code=ACCOUNT_ID,
        is_cloud_authenticated=True, exited_at=timezone.now(),
    )
    app = Application.objects.create(
        user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="a" * 40, port=8080, alloted_cpu=256, alloted_memory=512,
        status="ACTIVE",
        service_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service",
        target_group_arn=f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:targetgroup/my-app-tg/abc",
        task_definition_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:task-definition/my-app-task:1",
    )
    return app, infra


@pytest.fixture
def active_app(schema_db):
    """A twin fixture on a NOT-exited infra — used to prove exiting one tenant's
    infrastructure never affects another."""
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=2048, is_mock=True, code=ACCOUNT_ID,
        is_cloud_authenticated=True,
    )
    app = Application.objects.create(
        user=user, infrastructure=infra, name="other-app",
        project_remote_url="https://github.com/o/r2", project_branch="main",
        project_commit_hash="b" * 40, port=8080, alloted_cpu=256, alloted_memory=512,
        status="ACTIVE",
    )
    return app, infra


@pytest.fixture
def exited_sleeping_app(schema_db):
    """A SLEEPING app on an exited infra — used for the wake-view/service gating tests."""
    from django.utils import timezone

    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=2048, is_mock=True, code=ACCOUNT_ID,
        is_cloud_authenticated=True, exited_at=timezone.now(),
    )
    app = Application.objects.create(
        user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="a" * 40, port=8080, alloted_cpu=256, alloted_memory=512,
        status="SLEEPING", is_sleeping=True, desired_count=1,
        service_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service",
    )
    return app, infra


# ── app create / update ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_create_application_refused_on_exited_infra(exited_app):
    from api.services.application_service import ApplicationService
    from api.services.exit_enforcement import InfrastructureExitedError

    _app, infra = exited_app
    with pytest.raises(InfrastructureExitedError):
        ApplicationService().create_application(infra.user, {
            "name": "new-app", "infrastructure_id": str(infra.id),
            "project_remote_url": "https://github.com/o/new", "project_branch": "main",
            "alloted_cpu": 256, "alloted_memory": 512,
        })


@pytest.mark.django_db
def test_create_application_checks_permission_before_exit_state(exited_app):
    """A non-member must get PermissionError regardless of infra state — the exit check
    must never run first and leak "this infra has exited" to a stranger with the UUID."""
    from api.models.user import User
    from api.services.application_service import ApplicationService

    _app, infra = exited_app
    stranger = User.objects.create(id=uuid.uuid4(), email=f"s-{uuid.uuid4()}@x.com", user_name="s")

    with pytest.raises(PermissionError):
        ApplicationService().create_application(stranger, {
            "name": "new-app", "infrastructure_id": str(infra.id),
            "project_remote_url": "https://github.com/o/new", "project_branch": "main",
            "alloted_cpu": 256, "alloted_memory": 512,
        })


@pytest.mark.django_db
def test_create_application_view_returns_409(exited_app):
    from api.views.application import ApplicationListCreateView

    _app, infra = exited_app
    request = SimpleNamespace(data={
        "name": "new-app", "infrastructure_id": str(infra.id),
        "project_remote_url": "https://github.com/o/new", "project_branch": "main",
    }, user=infra.user)

    response = ApplicationListCreateView().post(request)

    assert response.status_code == 409
    assert response.data["code"] == "infrastructure_exited"


@pytest.mark.django_db
def test_update_application_refused_on_exited_infra(exited_app):
    from api.services.application_service import ApplicationService
    from api.services.exit_enforcement import InfrastructureExitedError

    app, infra = exited_app
    with pytest.raises(InfrastructureExitedError):
        ApplicationService().update_application(infra.user_id, str(app.id), {"description": "x"})


@pytest.mark.django_db
def test_update_application_checks_permission_before_exit_state(exited_app):
    from api.models.user import User
    from api.services.application_service import ApplicationService

    app, _infra = exited_app
    stranger = User.objects.create(id=uuid.uuid4(), email=f"s-{uuid.uuid4()}@x.com", user_name="s")

    with pytest.raises(PermissionError):
        ApplicationService().update_application(stranger.id, str(app.id), {"description": "x"})


@pytest.mark.django_db
def test_update_application_view_returns_409(exited_app):
    from api.views.application import ApplicationUpdateView

    app, infra = exited_app
    request = SimpleNamespace(user=SimpleNamespace(id=infra.user_id), data={"description": "x"})
    response = ApplicationUpdateView().patch(request, pk=str(app.id))

    assert response.status_code == 409
    assert response.data["code"] == "infrastructure_exited"


# ── deploy / retry ────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_deploy_view_returns_409_on_exited_infra(exited_app, monkeypatch):
    from api.views.application import ApplicationDeployView

    app, infra = exited_app
    monkeypatch.setattr(
        "api.services.infrastructure_permissions.InfrastructurePermissions.can_update_application",
        staticmethod(lambda *_a: True),
    )
    enqueue = MagicMock()
    monkeypatch.setattr("api.views.application.DeploymentQueue.enqueue_deployment", enqueue)

    response = ApplicationDeployView().post(_req(infra.user_id), pk=str(app.id))

    assert response.status_code == 409
    assert response.data["code"] == "infrastructure_exited"
    enqueue.assert_not_called()


@pytest.mark.django_db
def test_retry_view_returns_409_and_never_resets_arns(exited_app, monkeypatch):
    """Trap: the retry view resets ARNs to null and enqueues cleanup BEFORE enqueuing a
    fresh deploy. The exited check must run before any of that, not after."""
    from api.views.application import ApplicationRetryDeployView

    app, infra = exited_app
    original_service_arn = app.service_arn
    original_task_definition_arn = app.task_definition_arn
    monkeypatch.setattr(
        "api.services.infrastructure_permissions.InfrastructurePermissions.can_update_application",
        staticmethod(lambda *_a: True),
    )
    cleanup = MagicMock()
    deploy = MagicMock()
    monkeypatch.setattr("api.views.application.DeploymentQueue.enqueue_cleanup", cleanup)
    monkeypatch.setattr("api.views.application.DeploymentQueue.enqueue_deployment", deploy)

    response = ApplicationRetryDeployView().post(_req(infra.user_id), pk=str(app.id))

    assert response.status_code == 409
    assert response.data["code"] == "infrastructure_exited"
    cleanup.assert_not_called()
    deploy.assert_not_called()
    app.refresh_from_db()
    assert app.service_arn == original_service_arn
    assert app.task_definition_arn == original_task_definition_arn
    assert app.status == "ACTIVE"


# ── rollback / resume-auto-deploy ────────────────────────────────────────────

@pytest.mark.django_db
def test_trigger_rollback_refused_on_exited_infra(exited_app):
    from api.models.deployment import Deployment
    from api.services.deployment_snapshot import snapshot_env
    from api.services.exit_enforcement import InfrastructureExitedError
    from api.services.rollback_service import RollbackService

    app, infra = exited_app
    keys, digest = snapshot_env(app.id, {})
    target = Deployment.objects.create(
        application=app, image_tag="my-app-abc123", commit_sha="c" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=256, memory=512, port=8080,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )

    with pytest.raises(InfrastructureExitedError):
        RollbackService().trigger_rollback(infra.user_id, str(app.id), str(target.id))


@pytest.mark.django_db
def test_rollback_view_returns_409_on_exited_infra(exited_app):
    from api.models.deployment import Deployment
    from api.services.deployment_snapshot import snapshot_env
    from api.views.application import ApplicationRollbackView

    app, infra = exited_app
    keys, digest = snapshot_env(app.id, {})
    target = Deployment.objects.create(
        application=app, image_tag="my-app-abc123", commit_sha="c" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=256, memory=512, port=8080,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )

    response = ApplicationRollbackView().post(_req(infra.user_id), pk=str(app.id), deployment_id=str(target.id))

    assert response.status_code == 409
    assert response.data["code"] == "infrastructure_exited"


@pytest.mark.django_db
def test_resume_auto_deploy_refused_on_exited_infra(exited_app):
    from api.services.exit_enforcement import InfrastructureExitedError
    from api.services.rollback_service import RollbackService

    app, infra = exited_app
    app.auto_deploy_paused = True
    app.save(update_fields=["auto_deploy_paused"])

    with pytest.raises(InfrastructureExitedError):
        RollbackService().resume_auto_deploy(infra.user_id, str(app.id))

    app.refresh_from_db()
    assert app.auto_deploy_paused is True


@pytest.mark.django_db
def test_resume_auto_deploy_view_returns_409_on_exited_infra(exited_app):
    from api.views.application import ApplicationResumeAutoDeployView

    app, infra = exited_app
    app.auto_deploy_paused = True
    app.save(update_fields=["auto_deploy_paused"])

    response = ApplicationResumeAutoDeployView().post(_req(infra.user_id), pk=str(app.id))

    assert response.status_code == 409
    assert response.data["code"] == "infrastructure_exited"
    app.refresh_from_db()
    assert app.auto_deploy_paused is True


# ── GitHub webhook: ack without deploying ────────────────────────────────────

@pytest.mark.django_db
def test_webhook_acks_without_deploying_on_exited_infra(exited_app, monkeypatch):
    from api.views.application import application_github_webhook

    app, _infra = exited_app
    app.github_webhook_secret = "whsec-" + uuid.uuid4().hex
    app.save(update_fields=["github_webhook_secret"])
    enqueue = MagicMock()
    monkeypatch.setattr("api.views.application.DeploymentQueue.enqueue_deployment", enqueue)

    body = json.dumps({"ref": "refs/heads/main", "after": "d" * 40}).encode()
    sig = "sha256=" + hmac.new(app.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    request = APIRequestFactory().post(
        f"/api/v1/webhooks/github/{app.id}/", data=body, content_type="application/json",
        HTTP_X_GITHUB_EVENT="push", HTTP_X_HUB_SIGNATURE_256=sig,
    )

    response = application_github_webhook(request, app_id=str(app.id))

    assert response.status_code == 200
    assert response.data["reason"] == "infrastructure exited"
    enqueue.assert_not_called()


# ── custom-domain attach (internal) ──────────────────────────────────────────

@pytest.mark.django_db
def test_custom_domain_attach_returns_409_on_exited_infra(exited_app):
    from api.views.custom_domains_internal import custom_domain_attach

    app, infra = exited_app
    body = {
        "infrastructure_id": str(infra.id), "application_id": str(app.id),
        "hostname": "app.example.com", "cert_arn": "arn:aws:acm:us-east-1:123456789012:certificate/abc",
    }
    request = APIRequestFactory().post("/api/v1/internal/custom-domains/attach/", body, format="json")

    with patch("api.views.custom_domains_internal.attach_custom_domain") as mock_attach:
        response = custom_domain_attach(request)

    assert response.status_code == 409
    assert response.data["code"] == "infrastructure_exited"
    mock_attach.assert_not_called()


# ── backfill_host_routing: ineligible, never touched ─────────────────────────

@pytest.mark.django_db
def test_backfill_eligibility_reports_infrastructure_exited(exited_app):
    from api.services.application_deployment_service import ApplicationDeploymentService

    app, _infra = exited_app
    app.host_forward_rule_arn = None
    app.save(update_fields=["host_forward_rule_arn"])

    eligible, reason, ctx = ApplicationDeploymentService().evaluate_backfill_eligibility(app)

    assert eligible is False
    assert reason == "infrastructure_exited"
    assert ctx is None


# ── worker dequeue re-check: a job queued before exit must not run after ────

@pytest.mark.django_db
def test_worker_skips_a_deploy_job_when_infra_exited_at_dequeue_time(exited_app, monkeypatch):
    from api.management.commands.run_worker import execute_deploy_job

    app, _infra = exited_app
    ack = MagicMock()
    deploy = MagicMock()
    monkeypatch.setattr("api.services.deployment_queue.DeploymentQueue.ack_job", ack)
    monkeypatch.setattr(
        "api.services.application_deployment_service.ApplicationDeploymentService.deploy_application", deploy,
    )

    execute_deploy_job(str(app.id), {"app_id": str(app.id), "action": "deploy"})

    deploy.assert_not_called()
    ack.assert_called_once()


@pytest.mark.django_db
def test_worker_skips_a_rollback_job_when_infra_exited_at_dequeue_time(exited_app, monkeypatch):
    from api.management.commands.run_worker import execute_rollback_job
    from api.models.deployment import Deployment
    from api.services.deployment_snapshot import snapshot_env

    app, _infra = exited_app
    keys, digest = snapshot_env(app.id, {})
    target = Deployment.objects.create(
        application=app, image_tag="my-app-abc123", commit_sha="c" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=256, memory=512, port=8080,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )
    ack = MagicMock()
    rollback = MagicMock()
    monkeypatch.setattr("api.services.deployment_queue.DeploymentQueue.ack_job", ack)
    monkeypatch.setattr(
        "api.services.application_deployment_service.ApplicationDeploymentService.rollback_application", rollback,
    )

    execute_rollback_job(str(app.id), {"app_id": str(app.id), "action": "rollback", "deployment_id": str(target.id)})

    rollback.assert_not_called()
    ack.assert_called_once()


# ── app detail response exposes the flag ─────────────────────────────────────

@pytest.mark.django_db
def test_app_detail_exposes_infrastructure_exited_true(exited_app):
    from api.views.application import ApplicationDetailDeleteView

    app, infra = exited_app
    response = ApplicationDetailDeleteView().get(_req(infra.user_id), pk=str(app.id))

    assert response.status_code == 200
    assert response.data["infrastructure_exited"] is True


@pytest.mark.django_db
def test_app_detail_exposes_infrastructure_exited_false(active_app):
    from api.views.application import ApplicationDetailDeleteView

    app, infra = active_app
    response = ApplicationDetailDeleteView().get(_req(infra.user_id), pk=str(app.id))

    assert response.status_code == 200
    assert response.data["infrastructure_exited"] is False


# ── cross-tenant: exiting one infra never affects another ────────────────────

@pytest.mark.django_db
def test_deploying_on_a_non_exited_infra_is_unaffected(exited_app, active_app, monkeypatch):
    from api.views.application import ApplicationDeployView

    _exited_app, _exited_infra = exited_app
    app, infra = active_app
    monkeypatch.setattr(
        "api.services.infrastructure_permissions.InfrastructurePermissions.can_update_application",
        staticmethod(lambda *_a: True),
    )
    enqueue = MagicMock()
    monkeypatch.setattr("api.views.application.DeploymentQueue.enqueue_deployment", enqueue)

    response = ApplicationDeployView().post(_req(infra.user_id), pk=str(app.id))

    assert response.status_code == 202
    enqueue.assert_called_once()


# ── R1 (security review): exit mid-deploy/rollback must abort before the next
# ── AWS/k8s mutation, not just at dequeue time — a deploy/rollback can run for minutes.

@pytest.fixture
def deployable_app(schema_db):
    """A full ECS app + environment, NOT exited — mirrors test_rollback.py's ecs_app so a
    real deploy_application()/rollback_application() run can go through MockSession."""
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    from api.models.infrastructure import Infrastructure
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
        project_commit_hash="a" * 40, envs={"NODE_ENV": "production"},
        port=8080, alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
        status="ACTIVE",
        service_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service",
        target_group_arn=f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:targetgroup/my-app-tg/abc",
        task_definition_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:task-definition/my-app-task:1",
        deployment_url="http://alb.example.com/my-app",
    )
    return app, env, infra


def _exit_infra_now(infra_id):
    from django.utils import timezone

    from api.models.infrastructure import Infrastructure
    Infrastructure.objects.filter(id=infra_id).update(exited_at=timezone.now())


@pytest.mark.django_db
def test_deploy_aborts_if_infra_exits_mid_deploy_before_ecs_service_call(deployable_app, monkeypatch, settings):
    """Exit lands right after the task definition is registered — after the build already
    ran, before the ECS service is created/updated. Must abort there rather than only
    catching it at the next dequeue."""
    from api.mock.mock_session import MockSession
    from api.services.application_deployment_service import ApplicationDeploymentService
    from api.services.exit_enforcement import InfrastructureExitedError

    settings.PLATFORM_BASE_DOMAIN = None  # infra has no dns_label — host mode is out of scope here
    app, _env, infra = deployable_app
    service = ApplicationDeploymentService()
    session = MockSession(region="us-east-1", account_id=ACCOUNT_ID)
    monkeypatch.setattr(service, "_create_aws_session", lambda _infra: session)

    original_create_task_definition = service._create_task_definition

    def _create_task_definition_then_exit(*args, **kwargs):
        result = original_create_task_definition(*args, **kwargs)
        _exit_infra_now(infra.id)
        return result

    monkeypatch.setattr(service, "_create_task_definition", _create_task_definition_then_exit)

    with patch("aws.ecs.ECSClient.create_service") as create_service, \
         pytest.raises(InfrastructureExitedError):
        service.deploy_application(app)

    create_service.assert_not_called()
    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "exited" in app.error_message.lower()


@pytest.mark.django_db
def test_rollback_aborts_if_infra_exits_mid_rollback_before_update_service(deployable_app, monkeypatch):
    """Exit lands right after the rollback's task definition is registered, before
    update_service pins the ECS service to it."""
    from api.mock.mock_session import MockSession
    from api.models.deployment import Deployment
    from api.services.application_deployment_service import ApplicationDeploymentService
    from api.services.deployment_snapshot import snapshot_env
    from api.services.exit_enforcement import InfrastructureExitedError

    app, _env, infra = deployable_app
    keys, digest = snapshot_env(app.id, {})
    target = Deployment.objects.create(
        application=app, image_tag="my-app-" + "b" * 40, commit_sha="b" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=128, memory=256, port=3000,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )

    service = ApplicationDeploymentService()
    session = MockSession(region="us-east-1", account_id=ACCOUNT_ID)
    monkeypatch.setattr(service, "_create_aws_session", lambda _infra: session)

    original_create_task_definition = service._create_task_definition

    def _create_task_definition_then_exit(*args, **kwargs):
        result = original_create_task_definition(*args, **kwargs)
        _exit_infra_now(infra.id)
        return result

    monkeypatch.setattr(service, "_create_task_definition", _create_task_definition_then_exit)

    with patch("api.mock.mock_session.MockClient.update_service") as update_service, \
         pytest.raises(InfrastructureExitedError):
        service.rollback_application(app, target)

    update_service.assert_not_called()
    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "exited" in app.error_message.lower()


@pytest.mark.django_db
def test_deploy_abort_cleans_up_resources_already_created(deployable_app, monkeypatch, settings):
    """The exit check reuses the existing failure path (raise -> outer except) precisely
    so a mid-deploy abort still cleans up everything created so far, exactly like any other
    deploy failure — never a half-applied state worse than what the pre-existing failure
    path already leaves."""
    from api.mock.mock_session import MockSession
    from api.services.application_deployment_service import ApplicationDeploymentService
    from api.services.exit_enforcement import InfrastructureExitedError

    settings.PLATFORM_BASE_DOMAIN = None  # infra has no dns_label — host mode is out of scope here
    app, _env, infra = deployable_app
    service = ApplicationDeploymentService()
    session = MockSession(region="us-east-1", account_id=ACCOUNT_ID)
    monkeypatch.setattr(service, "_create_aws_session", lambda _infra: session)

    original_create_task_definition = service._create_task_definition

    def _create_task_definition_then_exit(*args, **kwargs):
        result = original_create_task_definition(*args, **kwargs)
        _exit_infra_now(infra.id)
        return result

    monkeypatch.setattr(service, "_create_task_definition", _create_task_definition_then_exit)

    with patch.object(service, "_cleanup_resource") as cleanup, \
         pytest.raises(InfrastructureExitedError):
        service.deploy_application(app)

    # The task definition and target group created before the abort are unwound, exactly
    # as they would be for any other exception raised at this point in the pipeline.
    cleaned_up_types = {call.args[1] for call in cleanup.call_args_list}
    assert "task_definition" in cleaned_up_types
    assert "target_group" in cleaned_up_types


# ── sleep / wake (security review RECOMMENDED 1) ─────────────────────────────

@pytest.mark.django_db
def test_sleep_view_returns_409_on_exited_infra(exited_app):
    from api.views.application import ApplicationSleepView

    app, infra = exited_app
    request = SimpleNamespace(user=SimpleNamespace(id=infra.user_id))

    response = ApplicationSleepView().post(request, pk=str(app.id))

    assert response.status_code == 409
    assert response.data["code"] == "infrastructure_exited"
    app.refresh_from_db()
    assert app.is_sleeping is False


@pytest.mark.django_db
def test_wake_view_returns_409_on_exited_infra(exited_sleeping_app):
    from api.views.application import ApplicationWakeView

    app, infra = exited_sleeping_app
    request = SimpleNamespace(user=SimpleNamespace(id=infra.user_id))

    response = ApplicationWakeView().post(request, pk=str(app.id))

    assert response.status_code == 409
    assert response.data["code"] == "infrastructure_exited"
    app.refresh_from_db()
    assert app.is_sleeping is True


@pytest.mark.django_db
def test_sleep_service_refuses_on_exited_infra(exited_app):
    from api.services.application_sleep_service import ApplicationSleepService
    from api.services.exit_enforcement import InfrastructureExitedError

    app, _infra = exited_app
    with pytest.raises(InfrastructureExitedError):
        ApplicationSleepService().sleep_application(app)


@pytest.mark.django_db
def test_wake_service_refuses_on_exited_infra(exited_sleeping_app):
    from api.services.application_sleep_service import ApplicationSleepService
    from api.services.exit_enforcement import InfrastructureExitedError

    app, _infra = exited_sleeping_app
    with pytest.raises(InfrastructureExitedError):
        ApplicationSleepService().wake_application(app)


@pytest.mark.django_db
def test_sleep_is_unaffected_on_a_non_exited_infra(active_app, monkeypatch):
    from api.views.application import ApplicationSleepView

    app, infra = active_app
    monkeypatch.setattr(
        "api.services.application_sleep_service.ApplicationSleepService.sleep_application",
        lambda self, _app: None,
    )
    request = SimpleNamespace(user=SimpleNamespace(id=infra.user_id))

    response = ApplicationSleepView().post(request, pk=str(app.id))

    assert response.status_code == 200
