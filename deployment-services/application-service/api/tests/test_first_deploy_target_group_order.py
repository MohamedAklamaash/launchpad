"""#43 moved ALB routing to after the ECS service is confirmed healthy — no traffic until
healthy, zero 502 window. On real AWS this breaks a brand-new target group: ECS
CreateService raises InvalidParameterException for a target group with no associated load
balancer yet, and an unattached target group is never health-checked either way. Every
first deploy failed this way; redeploys worked only because their target group was already
attached from a prior deploy.

ApplicationDeploymentService._create_ecs_service_with_routing picks the order based on
whether the target group is already attached — these tests exercise both orders end to end
against a mock strict enough to reject the old, broken one.
"""
import uuid

import pytest
from botocore.exceptions import ClientError

from api.common.naming import app_slug as _slug
from api.mock.mock_session import MockSession
from api.services.application_deployment_service import ApplicationDeploymentService

ACCOUNT_ID = "123456789012"


@pytest.fixture
def new_app(schema_db):
    """A genuinely first-time app: no target group, service, or task definition has ever
    been created for it — unlike test_rollback.py's ecs_app, which simulates an app
    already past its first deploy."""
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


def _spy_call_order(monkeypatch, service):
    """Wraps _configure_alb_routing and _create_ecs_service with spies that record which
    one actually ran first, while still calling through to the real implementation."""
    order = []
    original_route = service._configure_alb_routing
    original_create = service._create_ecs_service

    def _route_spy(*args, **kwargs):
        order.append("route")
        return original_route(*args, **kwargs)

    def _create_spy(*args, **kwargs):
        order.append("create_service")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(service, "_configure_alb_routing", _route_spy)
    monkeypatch.setattr(service, "_create_ecs_service", _create_spy)
    return order


# ── the mock enforces the AWS rule this bug hinges on ────────────────────────────────

def test_mock_rejects_create_service_for_a_target_group_with_no_load_balancer():
    session = MockSession(region="us-east-1", account_id=ACCOUNT_ID)
    ecs = session.client("ecs")
    target_group_arn = f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:targetgroup/my-app/abc"

    with pytest.raises(ClientError, match="does not have an associated load balancer"):
        ecs.create_service(
            serviceName="my-app-service",
            loadBalancers=[{"targetGroupArn": target_group_arn, "containerName": "c", "containerPort": 80}],
        )


def test_mock_describe_target_groups_reports_attachment_once_a_rule_forwards_to_it(monkeypatch):
    from aws.alb import ALBClient

    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    session = MockSession(region="us-east-1", account_id=ACCOUNT_ID)
    alb = ALBClient(session)
    listener_arn = alb.get_listener_arn(
        f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:loadbalancer/app/a/1", port=80,
    )
    target_group_arn = f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT_ID}:targetgroup/my-app/abc"

    before = alb.client.describe_target_groups(TargetGroupArns=[target_group_arn])["TargetGroups"][0]
    assert before["LoadBalancerArns"] == []

    alb.create_listener_rule(listener_arn, target_group_arn, "/my-app", priority=1000)

    after = alb.client.describe_target_groups(TargetGroupArns=[target_group_arn])["TargetGroups"][0]
    assert after["LoadBalancerArns"] != []


# ── first deploy: a brand-new target group must be routed before the service exists ──

@pytest.mark.django_db
def test_first_deploy_routes_the_new_target_group_before_creating_the_ecs_service(
    new_app, mock_service, monkeypatch, settings,
):
    settings.PLATFORM_BASE_DOMAIN = None
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    app, _env = new_app
    service, session = mock_service
    # The mock's DescribeServices otherwise reports any never-deleted name as already
    # ACTIVE (see MockClient.describe_services), which would route this deploy through
    # UpdateService — real AWS's actual first-deploy path is CreateService, since the
    # service genuinely doesn't exist yet. Marking it deleted up front (same technique
    # test_rollback.py uses for the F3 recreate path) forces the mock down that same
    # real-CreateService branch, so this test actually exercises the InvalidParameterException
    # guard the fix is protecting against.
    session.client("ecs").delete_service(service=f"{_slug(app.name)}-service")
    order = _spy_call_order(monkeypatch, service)

    url = service.deploy_application(app)

    assert order == ["route", "create_service"]
    app.refresh_from_db()
    assert app.status == "ACTIVE"
    assert app.target_group_arn is not None
    assert app.service_arn is not None
    assert url


@pytest.mark.django_db
def test_first_deploy_old_order_would_have_failed_against_the_stricter_mock(new_app, mock_service):
    """Regression guard: calling _create_ecs_service before the target group is routed —
    the pre-fix order — must raise, proving the fix (and not just a lenient mock) is what
    makes the deploy above succeed."""
    app, env = new_app
    service, session = mock_service

    target_group_arn = service._create_target_group(session, app, env)
    app.target_group_arn = target_group_arn
    app.save()
    session.client("ecs").delete_service(service=f"{_slug(app.name)}-service")

    with pytest.raises(ClientError, match="does not have an associated load balancer"):
        service._create_ecs_service(session, app, env)


# ── H2: exit must still abort before the route-first order's own AWS mutations ──────

@pytest.mark.django_db
def test_first_deploy_aborts_before_reserving_host_redirect_priority_if_infra_exits_first(
    new_app, mock_service, monkeypatch, settings,
):
    """A first deploy's new target group is routed BEFORE the service is created — the
    exit check must still catch an exit landing between target-group creation and that
    routing, not just inside _create_ecs_service. dns_label + a real platform domain are
    required, or _reserve_host_redirect_priority is a no-op and this would pass for the
    wrong reason."""
    from aws.alb import ALBClient
    from django.utils import timezone

    from api.models.infrastructure import Infrastructure
    from api.services.exit_enforcement import InfrastructureExitedError

    settings.PLATFORM_BASE_DOMAIN = "launchpad.example.com"
    app, env = new_app
    infra = app.infrastructure
    infra.dns_label = "0123456789abcdef"
    infra.save()
    service, session = mock_service

    original_create_target_group = service._create_target_group

    def _create_target_group_then_exit(*args, **kwargs):
        result = original_create_target_group(*args, **kwargs)
        Infrastructure.objects.filter(id=infra.id).update(exited_at=timezone.now())
        return result

    monkeypatch.setattr(service, "_create_target_group", _create_target_group_then_exit)

    with pytest.raises(InfrastructureExitedError):
        service.deploy_application(app)

    alb = ALBClient(session)
    listener_arn = alb.get_listener_arn(env.alb_arn, port=80)
    rules = alb.client.describe_rules(ListenerArn=listener_arn)["Rules"]
    assert all(r["Priority"] == "default" for r in rules)
    app.refresh_from_db()
    assert app.status == "FAILED"
    assert app.service_arn is None


# ── redeploy: an already-attached target group keeps the healthy-first order ────────

@pytest.mark.django_db
def test_redeploy_creates_and_waits_healthy_before_routing_when_target_group_already_attached(
    new_app, mock_service, monkeypatch, settings,
):
    settings.PLATFORM_BASE_DOMAIN = None
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    app, _env = new_app
    service, _session = mock_service

    service.deploy_application(app)  # first deploy attaches the target group to the ALB
    app.refresh_from_db()

    order = _spy_call_order(monkeypatch, service)
    url = service.deploy_application(app)

    assert order == ["create_service", "route"]
    app.refresh_from_db()
    assert app.status == "ACTIVE"
    assert url


# ── rollback recreate: a fresh target group must be routed before the service exists ─

@pytest.mark.django_db
def test_rollback_recreate_with_a_new_target_group_routes_before_creating_the_service(
    new_app, mock_service, monkeypatch, settings,
):
    from api.models.deployment import Deployment
    from api.services.deployment_snapshot import snapshot_env

    settings.PLATFORM_BASE_DOMAIN = None
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    app, env = new_app
    service, session = mock_service

    service.deploy_application(app)
    app.refresh_from_db()

    keys, digest = snapshot_env(app.id, app.envs or {})
    target = Deployment.objects.create(
        application=app, image_tag=f"{_slug(app.name)}-" + "a" * 40,
        commit_sha="a" * 40, tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=app.alloted_cpu, memory=app.alloted_memory, port=app.port,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )

    # Mirror ApplicationRetryDeployView's cleanup job: the ECS service, target group and
    # listener rule are all gone — the rollback must recreate every one of them. The
    # target group's name is a deterministic hash of infra+app id, so it must actually be
    # deleted from the mock's AWS state (not just unlinked on the row) or CreateTargetGroup
    # would just hand back the same, already-attached target group from the first deploy.
    session.client("ecs").delete_service(service=f"{_slug(app.name)}-service")
    elbv2 = session.client("elbv2")
    elbv2.delete_target_group(TargetGroupArn=app.target_group_arn)
    elbv2.delete_rule(RuleArn=app.listener_rule_arn)
    app.service_arn = None
    app.target_group_arn = None
    app.listener_rule_arn = None
    app.save(update_fields=["service_arn", "target_group_arn", "listener_rule_arn"])

    order = _spy_call_order(monkeypatch, service)
    url = service.rollback_application(app, target)

    assert order == ["route", "create_service"]
    app.refresh_from_db()
    assert app.status == "ACTIVE"
    assert app.service_arn is not None
    assert app.target_group_arn is not None
    assert url == f"http://{env.alb_dns}/my-app"
