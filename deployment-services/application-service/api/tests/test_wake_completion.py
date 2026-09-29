"""ApplicationSleepService wake completion (F-wake): `wake_application` only restores ECS
desiredCount and leaves the app DEPLOYING — nothing used to finish the job, so a woken app
stayed DEPLOYING forever and never showed its host_url (gated on ACTIVE). `wake_application`
now enqueues a job that waits for the restored service to actually become healthy
(`complete_wake`) before flipping it to ACTIVE, or FAILED with a sanitized message if it
never does.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api.mock.mock_session import MockSession
from api.services.application_deployment_service import ApplicationDeploymentService

ACCOUNT_ID = "123456789012"


@pytest.fixture
def sleeping_app(schema_db):
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
    Environment.objects.create(
        infrastructure=infra, status="ACTIVE", vpc_id="vpc-1",
        cluster_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:cluster/c",
        alb_arn=f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:loadbalancer/app/a/1",
        alb_dns="alb.example.com",
        ecr_repository_url=f"{ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc",
        ecs_task_execution_role_arn=f"arn:aws:iam::{ACCOUNT_ID}:role/exec",
    )
    app = Application.objects.create(
        user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="a" * 40, envs={}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
        status="ACTIVE", is_sleeping=True, desired_count=1,
        service_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service",
        target_group_arn=f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:targetgroup/my-app-tg/abc",
        task_definition_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:task-definition/my-app-task:1",
    )
    return app, infra


@pytest.fixture
def mock_boto_session(sleeping_app):
    _app, infra = sleeping_app
    return MockSession(region="us-east-1", account_id=ACCOUNT_ID, infra_id=str(infra.id))


# ── wake_application: enqueues the completion job ────────────────────────────────────────

@pytest.mark.django_db
def test_wake_enqueues_completion_job(sleeping_app, mock_boto_session, monkeypatch):
    from api.services import application_sleep_service as sleep_module
    from api.services.deployment_queue import DeploymentQueue

    app, _infra = sleeping_app
    monkeypatch.setattr(sleep_module, "create_boto3_session", lambda _infra: mock_boto_session)
    enqueue = MagicMock()
    monkeypatch.setattr(DeploymentQueue, "enqueue_wake", enqueue)

    sleep_module.ApplicationSleepService().wake_application(app)

    assert app.status == "DEPLOYING"
    assert app.is_sleeping is False
    enqueue.assert_called_once_with(app.id, app.infrastructure_id)


# ── complete_wake: the background job itself ─────────────────────────────────────────────

@pytest.mark.django_db
def test_complete_wake_marks_active_when_stable(sleeping_app, mock_boto_session, monkeypatch):
    from api.services.application_sleep_service import ApplicationSleepService

    app, _infra = sleeping_app
    app.is_sleeping = False
    app.status = "DEPLOYING"
    app.save(update_fields=["is_sleeping", "status"])
    monkeypatch.setattr(ApplicationDeploymentService, "_create_aws_session", lambda self, infra: mock_boto_session)

    ApplicationSleepService().complete_wake(app)

    app.refresh_from_db()
    assert app.status == "ACTIVE"
    assert app.error_message is None


@pytest.mark.django_db
def test_complete_wake_marks_failed_on_timeout(sleeping_app, mock_boto_session, monkeypatch):
    from api.services.application_sleep_service import ApplicationSleepService

    app, _infra = sleeping_app
    app.is_sleeping = False
    app.status = "DEPLOYING"
    app.save(update_fields=["is_sleeping", "status"])
    monkeypatch.setattr(ApplicationDeploymentService, "_create_aws_session", lambda self, infra: mock_boto_session)

    def _timeout(self, *args, **kwargs):
        raise Exception("Service my-app-service did not become stable within 300 seconds")

    monkeypatch.setattr(ApplicationDeploymentService, "_wait_for_service_stable_with_refresh", _timeout)

    ApplicationSleepService().complete_wake(app)

    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "did not become stable" in app.error_message


@pytest.mark.django_db
def test_complete_wake_refuses_on_exited_infra(sleeping_app):
    from django.utils import timezone

    from api.services.application_sleep_service import ApplicationSleepService

    app, infra = sleeping_app
    app.is_sleeping = False
    app.status = "DEPLOYING"
    app.save(update_fields=["is_sleeping", "status"])
    infra.exited_at = timezone.now()
    infra.save(update_fields=["exited_at"])

    ApplicationSleepService().complete_wake(app)

    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "exited" in app.error_message


@pytest.mark.django_db
def test_complete_wake_refuses_when_infra_exits_mid_wait(sleeping_app, mock_boto_session, monkeypatch):
    """Regression: the exit re-check right before the final ACTIVE write must read the
    infrastructure fresh from the DB, not the in-memory object fetched before the wait —
    otherwise an exit that lands while the wait is running is invisible to it."""
    from django.utils import timezone

    from api.models.infrastructure import Infrastructure
    from api.services.application_sleep_service import ApplicationSleepService

    app, infra = sleeping_app
    app.is_sleeping = False
    app.status = "DEPLOYING"
    app.save(update_fields=["is_sleeping", "status"])
    monkeypatch.setattr(ApplicationDeploymentService, "_create_aws_session", lambda self, infra: mock_boto_session)

    def _exit_during_wait(self, *args, **kwargs):
        Infrastructure.objects.filter(id=infra.id).update(exited_at=timezone.now())

    monkeypatch.setattr(ApplicationDeploymentService, "_wait_for_service_stable_with_refresh", _exit_during_wait)

    ApplicationSleepService().complete_wake(app)

    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "exited" in app.error_message


# ── run_worker: exited infra dequeued for wake is refused, not run ───────────────────────

@pytest.mark.django_db
def test_execute_wake_job_refuses_exited_infra_and_acks(sleeping_app, monkeypatch):
    from django.utils import timezone

    from api.management.commands.run_worker import execute_wake_job
    from api.services.deployment_queue import DeploymentQueue

    app, infra = sleeping_app
    app.is_sleeping = False
    app.status = "DEPLOYING"
    app.save(update_fields=["is_sleeping", "status"])
    infra.exited_at = timezone.now()
    infra.save(update_fields=["exited_at"])

    ack = MagicMock()
    monkeypatch.setattr(DeploymentQueue, "ack_job", ack)

    execute_wake_job(str(app.id), {"app_id": str(app.id), "action": "wake"})

    ack.assert_called_once()
    app.refresh_from_db()
    assert app.status == "FAILED"


# ── view: response status matches the stored status, not a hardcoded 'ACTIVE' ────────────

@pytest.mark.django_db
def test_wake_view_returns_the_stored_status(sleeping_app, monkeypatch):
    from api.views.application import ApplicationWakeView

    app, infra = sleeping_app

    def _fake_wake(self, application):
        application.status = "DEPLOYING"
        application.is_sleeping = False
        application.save(update_fields=["status", "is_sleeping"])

    monkeypatch.setattr(
        "api.services.application_sleep_service.ApplicationSleepService.wake_application",
        _fake_wake,
    )
    request = SimpleNamespace(user=SimpleNamespace(id=infra.user_id))

    response = ApplicationWakeView().post(request, pk=str(app.id))

    assert response.status_code == 200
    assert response.data["status"] == "DEPLOYING"
