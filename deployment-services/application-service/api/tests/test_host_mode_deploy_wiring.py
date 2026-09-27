"""F1b part 3a: wiring host-mode routing into the ECS deploy flow.

Exercises the real ALBClient against the mock AWS session (api/mock/mock_session.py) rather
than a patched ALBClient — the point is to prove the actual boto3-call sequence (describe,
create_rule, set_rule_priorities) works end to end against something shaped like the real
API, not just that the right method names were called.
"""
import base64
import uuid
from unittest.mock import patch

import pytest
from aws.alb import ALBClient
from aws.container_config import HOST_MODE_HEALTH_CHECK_PATH, generate_nginx_config
from aws.ecs import ECSClient


@pytest.fixture
def deploy_fixtures(schema_db):
    from api.mock.mock_session import MockSession
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User
    from api.services.application_deployment_service import ApplicationDeploymentService

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512,
        dns_label="0123456789abcdef", tls_status="ISSUED", dns_synced=True, https_ready=True,
    )
    env = Environment.objects.create(
        id=uuid.uuid4(), infrastructure=infra,
        ecr_repository_url="123456789012.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc",
        ecs_task_execution_role_arn="arn:aws:iam::123456789012:role/exec",
        alb_arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/x/1",
    )
    app = Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    session = MockSession(region="us-east-1", account_id="123456789012", infra_id=str(infra.id))
    return ApplicationDeploymentService(), session, app, env, infra


# ── _resolve_host_routing ────────────────────────────────────────────────────────────────

def test_resolve_host_routing_true_when_infra_is_tls_ready(deploy_fixtures, settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, _infra = deploy_fixtures

    host_mode, hostname, reason = service._resolve_host_routing(app, env, session)

    assert host_mode is True
    assert hostname == "my-app.0123456789abcdef.launchpad.aklamaash.me"
    assert reason is None


def test_resolve_host_routing_false_when_tls_not_issued(deploy_fixtures, settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, infra = deploy_fixtures
    infra.tls_status = "PENDING"
    infra.save()

    host_mode, hostname, reason = service._resolve_host_routing(app, env, session)

    assert (host_mode, hostname, reason) == (False, None, "tls_not_issued")


def test_resolve_host_routing_false_when_slug_is_not_a_single_dns_label(deploy_fixtures, settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, _infra = deploy_fixtures
    app.name = "my.app"
    app.save()

    host_mode, hostname, reason = service._resolve_host_routing(app, env, session)

    assert (host_mode, hostname) == (False, None)
    assert reason == "slug_not_hostname_safe"


def test_resolve_host_routing_false_without_a_platform_domain(deploy_fixtures, settings):
    settings.PLATFORM_BASE_DOMAIN = None
    service, session, app, env, _infra = deploy_fixtures

    host_mode, hostname, reason = service._resolve_host_routing(app, env, session)

    assert (host_mode, hostname, reason) == (False, None, "platform_domain_unconfigured")


# ── _configure_host_routing: real ALBClient against the mock session ────────────────────

def test_host_mode_deploy_creates_forward_and_redirect_rules(deploy_fixtures, settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, _infra = deploy_fixtures
    app.target_group_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/my-app/abc"
    app.save()
    alb = ALBClient(session)
    hostname = "my-app.0123456789abcdef.launchpad.aklamaash.me"

    rule_arn = service._configure_host_routing(alb, app, env, True, hostname)

    app.refresh_from_db()
    assert app.host_forward_rule_arn == rule_arn

    https_listener = alb.get_listener_arn(env.alb_arn, port=443)
    http_listener = alb.get_listener_arn(env.alb_arn, port=80)

    forward_rules = alb.client.describe_rules(ListenerArn=https_listener)["Rules"]
    forward = next(r for r in forward_rules if r["RuleArn"] == rule_arn)
    assert forward["Conditions"] == [{"Field": "host-header", "Values": [hostname]}]
    assert forward["Actions"] == [{"Type": "forward", "TargetGroupArn": app.target_group_arn}]

    redirect_rules = alb.client.describe_rules(ListenerArn=http_listener)["Rules"]
    redirect = next(
        r for r in redirect_rules
        if r["Conditions"] and r["Conditions"][0]["Values"] == ["*.0123456789abcdef.launchpad.aklamaash.me"]
    )
    assert redirect["Priority"] == "1"
    assert redirect["Actions"][0]["Type"] == "redirect"
    assert redirect["Actions"][0]["RedirectConfig"]["Protocol"] == "HTTPS"


def test_path_mode_never_creates_a_443_rule(deploy_fixtures):
    service, session, app, env, _infra = deploy_fixtures
    alb = ALBClient(session)

    result = service._configure_host_routing(alb, app, env, False, None)

    assert result is None
    https_listener = alb.get_listener_arn(env.alb_arn, port=443)
    assert alb.client.describe_rules(ListenerArn=https_listener)["Rules"] == [
        {"RuleArn": alb.client._arn("listener-rule/default"), "Priority": "default", "Actions": [], "Conditions": []}
    ]


def test_downgrading_out_of_host_mode_removes_the_stale_forward_rule(deploy_fixtures, settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, _infra = deploy_fixtures
    app.target_group_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/my-app/abc"
    app.save()
    alb = ALBClient(session)
    hostname = "my-app.0123456789abcdef.launchpad.aklamaash.me"
    service._configure_host_routing(alb, app, env, True, hostname)
    app.refresh_from_db()
    assert app.host_forward_rule_arn is not None

    service._configure_host_routing(alb, app, env, False, None)

    app.refresh_from_db()
    assert app.host_forward_rule_arn is None


def test_redeploy_in_host_mode_does_not_duplicate_the_infra_wide_redirect_rule(deploy_fixtures, settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, _infra = deploy_fixtures
    app.target_group_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/my-app/abc"
    app.save()
    alb = ALBClient(session)
    hostname = "my-app.0123456789abcdef.launchpad.aklamaash.me"

    service._configure_host_routing(alb, app, env, True, hostname)
    service._configure_host_routing(alb, app, env, True, hostname)

    http_listener = alb.get_listener_arn(env.alb_arn, port=80)
    wildcard = "*.0123456789abcdef.launchpad.aklamaash.me"
    matches = [
        r for r in alb.client.describe_rules(ListenerArn=http_listener)["Rules"]
        if r["Conditions"] and r["Conditions"][0].get("Values") == [wildcard]
    ]
    assert len(matches) == 1


# ── R2: cleanup on deploy failure must clear host_forward_rule_arn ──────────────────────

def test_cleanup_of_host_forward_rule_clears_the_db_field(deploy_fixtures, settings):
    """host_forward_rule_arn is saved before verify_target_group_attached runs — if that
    step (or anything after it) fails, the unwind deletes the AWS rule but must also clear
    the now-dangling ARN, or app_host_url() would keep reporting a host URL that no longer
    routes anywhere."""
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, _infra = deploy_fixtures
    app.target_group_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/my-app/abc"
    app.save()
    alb = ALBClient(session)
    hostname = "my-app.0123456789abcdef.launchpad.aklamaash.me"
    rule_arn = service._configure_host_routing(alb, app, env, True, hostname)
    app.refresh_from_db()
    assert app.host_forward_rule_arn == rule_arn

    service._cleanup_resource(session, 'host_forward_rule', rule_arn, app, env)

    app.refresh_from_db()
    assert app.host_forward_rule_arn is None


def test_cleanup_of_host_forward_rule_clears_the_field_even_if_already_deleted(deploy_fixtures, settings):
    """The rule may already be gone (deleted earlier in the same unwind, or by a previous
    failed attempt) — RuleNotFound must not prevent the DB field from being cleared."""
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, _infra = deploy_fixtures
    app.host_forward_rule_arn = "arn:aws:elasticloadbalancing:::listener-rule/already-gone"
    app.save()

    service._cleanup_resource(session, 'host_forward_rule', app.host_forward_rule_arn, app, env)

    app.refresh_from_db()
    assert app.host_forward_rule_arn is None


# ── R1: _reserve_host_redirect_priority closes the first-deploy window ─────────────────

def test_reserve_host_redirect_priority_wins_even_against_a_path_rule_created_afterward(
    deploy_fixtures, settings, monkeypatch,
):
    """The redirect rule must be reserved BEFORE _configure_alb_routing ever creates a path
    rule — this is the deploy-flow-level regression test for R1's "close the first-deploy
    window" fix. Runs even though this particular app is in path mode (its own eligibility
    is irrelevant to whether the infra-wide redirect gets reserved)."""
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, _infra = deploy_fixtures
    app.target_group_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/my-app/abc"
    app.save()
    alb = ALBClient(session)

    service._reserve_host_redirect_priority(alb, app, env)
    listener_rule_arn, listener_arn = service._configure_alb_routing(session, app, env)

    rules = alb.client.describe_rules(ListenerArn=listener_arn)["Rules"]
    wildcard = "*.0123456789abcdef.launchpad.aklamaash.me"
    redirect_rule = next(r for r in rules if r["Conditions"] and r["Conditions"][0].get("Values") == [wildcard])
    path_rule = next(r for r in rules if r["RuleArn"] == listener_rule_arn)

    assert redirect_rule["Priority"] == "1"
    assert path_rule["Priority"] != "1"


def test_reserve_host_redirect_priority_is_a_noop_without_a_dns_label(deploy_fixtures, settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"
    service, session, app, env, infra = deploy_fixtures
    infra.dns_label = None
    infra.save()
    alb = ALBClient(session)

    service._reserve_host_redirect_priority(alb, app, env)

    listener_arn = alb.get_listener_arn(env.alb_arn, port=80)
    assert alb.client.describe_rules(ListenerArn=listener_arn)["Rules"] == [
        {"RuleArn": alb.client._arn("listener-rule/default"), "Priority": "default", "Actions": [], "Conditions": []}
    ]


def test_configure_alb_routing_uses_exact_and_prefixed_path_conditions(deploy_fixtures, monkeypatch):
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    service, session, app, env, _infra = deploy_fixtures
    app.target_group_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/my-app/abc"
    app.save()
    alb = ALBClient(session)

    listener_rule_arn, listener_arn = service._configure_alb_routing(session, app, env)

    rules = alb.client.describe_rules(ListenerArn=listener_arn)["Rules"]
    path_rule = next(r for r in rules if r["RuleArn"] == listener_rule_arn)
    assert path_rule["Conditions"][0]["Values"] == ["/my-app", "/my-app/*"]


# ── task definition: host-mode nginx config + health-path lockstep ──────────────────────

def test_task_definition_in_host_mode_bakes_the_hostname_into_nginx_config(deploy_fixtures):
    service, session, app, env, _infra = deploy_fixtures
    hostname = "my-app.0123456789abcdef.launchpad.aklamaash.me"

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(
            session, app, env, resolved_sha="a" * 40, host_mode=True, app_hostname=hostname,
        )

    kwargs = ecs_cls.return_value.create_task_definition.call_args.kwargs
    assert kwargs["host_mode"] is True
    assert kwargs["app_hostname"] == hostname


def test_ecs_client_host_mode_config_matches_container_config_directly():
    """Golden equivalence: ECSClient is a pure pass-through to container_config's
    generate_nginx_config — no host-mode-specific logic duplicated in aws/ecs.py."""
    hostname = "myapp.abc123.launchpad.app"
    ecs = ECSClient.__new__(ECSClient)

    via_ecs = ecs._generate_nginx_config("myapp", 8080, host_mode=True, app_hostname=hostname)
    direct = generate_nginx_config("myapp", 8080, host_mode=True, app_hostname=hostname)

    assert via_ecs == direct
    assert HOST_MODE_HEALTH_CHECK_PATH in via_ecs


def test_ecs_client_path_mode_is_byte_identical_to_before_this_feature():
    """Golden non-regression: path mode (the default) must never change shape."""
    ecs = ECSClient.__new__(ECSClient)

    via_ecs = ecs._generate_nginx_config("myapp", 8080)
    direct = generate_nginx_config("myapp", 8080)

    assert via_ecs == direct
    assert "server_name" not in via_ecs


def test_create_task_definition_host_mode_nginx_config_is_reachable_via_task_def(deploy_fixtures):
    """End-to-end through the real ECS client (against a mocked boto3 elbv2/ecs client): the
    base64 blob ECS actually receives decodes to a config carrying this app's own hostname
    and the host-mode health path."""
    from unittest.mock import MagicMock

    from aws.ecs import ECSClient as RealECSClient

    service, session, app, env, _infra = deploy_fixtures
    hostname = "my-app.0123456789abcdef.launchpad.aklamaash.me"

    with patch("api.services.application_deployment_service.ECSClient", RealECSClient):
        real_client = RealECSClient.__new__(RealECSClient)
        real_client.client = MagicMock()
        real_client.client.register_task_definition.return_value = {
            "taskDefinition": {"taskDefinitionArn": "arn:x"}
        }
        with patch("api.services.application_deployment_service.ECSClient", return_value=real_client):
            service._create_task_definition(
                session, app, env, resolved_sha="a" * 40, host_mode=True, app_hostname=hostname,
            )

    container_defs = real_client.client.register_task_definition.call_args.kwargs["containerDefinitions"]
    nginx_container = next(c for c in container_defs if c["name"].endswith("-nginx"))
    nginx_config_b64 = next(
        e["value"] for e in nginx_container["environment"] if e["name"] == "NGINX_CONFIG_B64"
    )
    decoded = base64.b64decode(nginx_config_b64).decode()
    assert f"server_name {hostname};" in decoded
    assert HOST_MODE_HEALTH_CHECK_PATH in decoded
