"""aws/ecs.py wait_for_service_stable — real-AWS incident (e2e-web).

`nginx: [emerg] could not build server_names_hash` made a redeployed host-mode task's
nginx sidecar exit 0 on startup. ECS then ran a normal rolling update: the old (ACTIVE)
deployment's task kept running — and stayed attached to the target group — while the new
(PRIMARY) deployment's task kept failing. At the *service* level that read as
runningCount == desiredCount == 1, so the old wait_for_service_stable (keyed on those
service-wide fields) reported "stable", the app was marked ACTIVE, and host_url was
published for a deploy that never actually shipped anything new.

The fix keys on the PRIMARY deployment's own runningCount/desiredCount/rolloutState and
requires every other (ACTIVE) deployment to be fully drained. These tests exercise it
through the real outer boundary — ApplicationDeploymentService.rollback_application and
.deploy_application, both of which take the same update_service path a redeploy does —
against MockSession, not by calling ECSClient.wait_for_service_stable directly.
"""
import uuid

import pytest

from api.mock.mock_session import MockSession
from api.services.application_deployment_service import ApplicationDeploymentService

ACCOUNT_ID = "123456789012"


@pytest.fixture
def ecs_app(schema_db):
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
        project_commit_hash="a" * 40, envs={"NODE_ENV": "production"},
        port=8080, alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
        status="ACTIVE",
        service_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:service/c/my-app-service",
        target_group_arn=f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:targetgroup/my-app-tg/abc",
        task_definition_arn=f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:task-definition/my-app-task:1",
        deployment_url="http://alb.example.com/my-app",
    )
    return app, env


@pytest.fixture
def old_deployment(ecs_app):
    from api.models.deployment import Deployment
    from api.services.deployment_snapshot import snapshot_env

    app, _env = ecs_app
    keys, digest = snapshot_env(app.id, {"NODE_ENV": "staging"})
    return Deployment.objects.create(
        application=app, image_tag="my-app-" + "b" * 40, commit_sha="b" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=128, memory=256, port=3000,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )


@pytest.fixture
def new_app(schema_db):
    """A genuinely first-time app (same shape as
    test_first_deploy_target_group_order.py's fixture of the same name) — used to exercise
    the stuck-rollout check through deploy_application rather than rollback_application.
    The mock's describe_services reports any never-deleted service name as already ACTIVE,
    so this still takes the update_service (redeploy) path deploy_application would take
    against a real, already-onboarded infra."""
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


@pytest.mark.django_db
def test_stuck_primary_with_old_task_still_running_fails_the_deploy(
    ecs_app, old_deployment, mock_service, monkeypatch, settings,
):
    """The exact e2e-web shape: PRIMARY never converges (failedTasks > 0) while ACTIVE
    (the previous deployment) still has its task running. Service-wide runningCount(1) ==
    desiredCount(1) here — the bug this guards against reported that as stable."""
    settings.PLATFORM_BASE_DOMAIN = None
    monkeypatch.setenv("ECS_SERVICE_STABLE_POLL_INTERVAL", "0")
    app, _env = ecs_app
    service, session = mock_service
    # _rollback_ecs calls _wait_for_service_stable_with_refresh with no timeout override,
    # so it always uses that method's own 300s default — irrelevant to what's actually
    # being tested here (the PRIMARY-vs-service-wide check), and too slow for a unit test.
    original_wait = service._wait_for_service_stable_with_refresh
    monkeypatch.setattr(
        service, "_wait_for_service_stable_with_refresh",
        lambda infra, cluster_arn, service_name, timeout=2, **kw: original_wait(
            infra, cluster_arn, service_name, timeout=timeout, **kw,
        ),
    )
    session._service_deployments["my-app-service"] = [
        {
            "status": "PRIMARY", "rolloutState": "IN_PROGRESS", "failedTasks": 1,
            "runningCount": 0, "desiredCount": 1,
        },
        {
            "status": "ACTIVE", "rolloutState": "COMPLETED", "failedTasks": 0,
            "runningCount": 1, "desiredCount": 1,
        },
    ]

    with pytest.raises(Exception, match="new tasks failed to start"):
        service.rollback_application(app, old_deployment)

    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "new tasks failed to start" in app.error_message


@pytest.mark.django_db
def test_completed_rollout_with_old_deployment_drained_marks_active(
    ecs_app, old_deployment, mock_service, settings,
):
    """The healthy case: PRIMARY converged and the previous deployment has nothing left
    running. Includes an explicit drained ACTIVE deployment (not just a lone PRIMARY, which
    the mock's own default already looks like) so this test actually exercises the
    old_deployments_drained check rather than passing on the mock's default shape alone."""
    settings.PLATFORM_BASE_DOMAIN = None
    app, _env = ecs_app
    service, session = mock_service
    session._service_deployments["my-app-service"] = [
        {
            "status": "PRIMARY", "rolloutState": "COMPLETED", "failedTasks": 0,
            "runningCount": 1, "desiredCount": 1,
        },
        {
            "status": "ACTIVE", "rolloutState": "COMPLETED", "failedTasks": 0,
            "runningCount": 0, "desiredCount": 0,
        },
    ]

    url = service.rollback_application(app, old_deployment)

    app.refresh_from_db()
    assert app.status == "ACTIVE"
    assert url


@pytest.mark.django_db
def test_stuck_primary_fails_deploy_application_before_publishing_a_host_url(
    new_app, mock_service, monkeypatch, settings,
):
    """Same shape, through deploy_application (a redeploy) rather than rollback: a stuck
    PRIMARY next to a still-running old deployment must fail the deploy, leave the app
    FAILED, and never reach the host_forward_rule_arn/deployment_url writes that only
    happen after this wait succeeds."""
    settings.PLATFORM_BASE_DOMAIN = None
    app, _env = new_app
    service, session = mock_service
    original_wait = service._wait_for_service_stable_with_refresh
    monkeypatch.setattr(
        service, "_wait_for_service_stable_with_refresh",
        lambda infra, cluster_arn, service_name, timeout=2, **kw: original_wait(
            infra, cluster_arn, service_name, timeout=timeout, **kw,
        ),
    )
    session._service_deployments["my-app-service"] = [
        {
            "status": "PRIMARY", "rolloutState": "IN_PROGRESS", "failedTasks": 1,
            "runningCount": 0, "desiredCount": 1,
        },
        {
            "status": "ACTIVE", "rolloutState": "COMPLETED", "failedTasks": 0,
            "runningCount": 1, "desiredCount": 1,
        },
    ]

    with pytest.raises(Exception, match="new tasks failed to start"):
        service.deploy_application(app)

    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "new tasks failed to start" in app.error_message
    assert app.host_forward_rule_arn is None
    assert app.deployment_url is None


@pytest.mark.django_db
def test_circuit_breaker_rollback_onto_old_task_definition_fails_the_deploy(
    ecs_app, old_deployment, mock_service, settings,
):
    """ECS's own deploymentCircuitBreaker (enable=True, rollback=True — see
    ECSClient.create_service/update_service) can silently converge the service back onto
    the PREVIOUS task definition when the new one keeps failing. That rollback is its own
    deployment; once it converges, PRIMARY looks completely healthy (COMPLETED,
    runningCount == desiredCount, nothing else running) — every check except the
    expected_task_definition_arn one would call this "stable"."""
    settings.PLATFORM_BASE_DOMAIN = None
    app, _env = ecs_app
    service, session = mock_service
    # A genuinely different task definition from whatever the mock will register for this
    # rollback (the mock's register_task_definition is deterministic per family/revision,
    # so it would otherwise coincidentally match app.task_definition_arn) — the point is
    # that PRIMARY reports running something other than what this call just asked for.
    stale_task_definition_arn = (
        f"arn:aws:ecs:us-east-1:{ACCOUNT_ID}:task-definition/my-app-task:0"
    )
    session._service_deployments["my-app-service"] = [
        {
            "status": "PRIMARY", "rolloutState": "COMPLETED", "failedTasks": 0,
            "runningCount": 1, "desiredCount": 1,
            "taskDefinition": stale_task_definition_arn,
        },
    ]

    with pytest.raises(Exception, match="ECS rolled back to the previous one"):
        service.rollback_application(app, old_deployment)

    app.refresh_from_db()
    assert app.status == "FAILED"
    assert "ECS rolled back to the previous one" in app.error_message
    # The rollback DID register a new task definition (task_def_arn, set before the wait
    # ran) — it's just not the one ECS actually converged the service onto.
    assert app.task_definition_arn != stale_task_definition_arn
