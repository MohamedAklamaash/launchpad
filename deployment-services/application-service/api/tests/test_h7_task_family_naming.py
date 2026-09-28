"""H7 residual from H4: `ECSClient.create_task_definition`'s family (`{slug}-task`) was
still shared across infras with the same app slug — not a truncated-UUID issue, but the
same class of collision H4 already fixed for target groups and log groups. Mirrors that
fix exactly: a new app gets a hashed family (`new_ecs_task_family`), persisted to
`Application.task_family`, with a legacy fallback (`ecs_task_family_for`) for apps that
deployed before this field existed. Every reader — deploy, rollback (via
`_create_task_definition`), the ECS service's container name, runtime-log tailing, and
the exit-export inventory — must read the stored value back rather than recomputing it.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.common.naming import ecs_task_family, ecs_task_family_for, new_ecs_task_family

_SHARED_PREFIX_INFRA_A = uuid.UUID("019f2c2b-9b19-71a9-8f3e-cd81d13effb9")
_SHARED_PREFIX_INFRA_B = uuid.UUID("019f2c2b-1111-71a9-8f3e-cd81d13effb9")


def _fake_app(*, infrastructure_id, application_id, name="my-app", task_family=None):
    return SimpleNamespace(
        infrastructure_id=infrastructure_id, id=application_id, name=name,
        task_family=task_family,
    )


# ── naming helpers ───────────────────────────────────────────────────────────────────

def test_new_ecs_task_family_distinct_for_two_infras_sharing_a_uuid_prefix():
    app_id = uuid.uuid4()
    family_a = new_ecs_task_family(_fake_app(infrastructure_id=_SHARED_PREFIX_INFRA_A, application_id=app_id))
    family_b = new_ecs_task_family(_fake_app(infrastructure_id=_SHARED_PREFIX_INFRA_B, application_id=app_id))
    assert family_a != family_b


def test_ecs_task_family_for_falls_back_to_legacy_when_nothing_is_stored():
    app = _fake_app(infrastructure_id=uuid.uuid4(), application_id=uuid.uuid4(), name="my-app")
    assert ecs_task_family_for(app) == ecs_task_family("my-app")


def test_ecs_task_family_for_prefers_the_stored_name():
    stored = "my-app-deadbeef-task"
    app = _fake_app(
        infrastructure_id=uuid.uuid4(), application_id=uuid.uuid4(), name="my-app",
        task_family=stored,
    )
    assert ecs_task_family_for(app) == stored


# ── _create_task_definition call site ───────────────────────────────────────────────

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
    )
    env = Environment.objects.create(
        id=uuid.uuid4(), infrastructure=infra,
        ecr_repository_url="123456789012.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc",
        ecs_task_execution_role_arn="arn:aws:iam::123456789012:role/exec",
        vpc_id="vpc-1", alb_security_group_id="sg-alb",
        alb_arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/alb/1",
    )
    app = Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    session = MockSession(region="us-east-1", account_id="123456789012", infra_id=str(infra.id))
    return ApplicationDeploymentService(), session, app, env


@pytest.mark.django_db
def test_first_deploy_of_a_new_app_persists_a_hashed_task_family(deploy_fixtures):
    service, session, app, env = deploy_fixtures
    assert app.task_family is None
    assert app.task_definition_arn is None

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    expected = new_ecs_task_family(app)
    app.refresh_from_db()
    assert app.task_family == expected
    assert ecs_cls.return_value.create_task_definition.call_args.kwargs["family"] == expected


def _record_succeeded_deployment(app):
    from api.models.deployment import Deployment
    from api.services.deployment_snapshot import snapshot_env

    keys, digest = snapshot_env(app.id, {})
    Deployment.objects.create(
        application=app, image_tag="my-app-" + "a" * 40, commit_sha="a" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=keys, env_values_hash=digest, attached_database_ids=[],
        cpu=256, memory=512, port=8080,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )


@pytest.mark.django_db
def test_redeploy_of_a_pre_h7_app_keeps_the_legacy_task_family(deploy_fixtures):
    service, session, app, env = deploy_fixtures
    app.task_definition_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app-task:3"
    app.save(update_fields=["task_definition_arn"])
    _record_succeeded_deployment(app)

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    legacy = ecs_task_family("my-app")
    app.refresh_from_db()
    assert app.task_family == legacy
    assert ecs_cls.return_value.create_task_definition.call_args.kwargs["family"] == legacy


@pytest.mark.django_db
def test_subsequent_deploy_reuses_the_already_stored_task_family(deploy_fixtures):
    service, session, app, env = deploy_fixtures
    stored = "my-app-cafebabe-task"
    app.task_family = stored
    app.task_definition_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app-task:1"
    app.save(update_fields=["task_family", "task_definition_arn"])

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    app.refresh_from_db()
    assert app.task_family == stored
    assert ecs_cls.return_value.create_task_definition.call_args.kwargs["family"] == stored


# ── _create_ecs_service call site: container_name must match the stored family ──────

class _FakeEC2:
    def describe_subnets(self, **kwargs):
        return {"Subnets": [{"SubnetId": "subnet-1"}]}

    def authorize_security_group_ingress(self, **kwargs):
        return {}


class _SessionWithEC2:
    def __init__(self, ec2):
        self._ec2 = ec2

    def client(self, name, **kwargs):
        assert name == "ec2"
        return self._ec2


@pytest.mark.django_db
def test_create_ecs_service_container_name_matches_the_stored_task_family(deploy_fixtures, monkeypatch):
    service, _session, app, env = deploy_fixtures
    app.task_family = "my-app-cafebabe-task"
    app.save(update_fields=["task_family"])
    monkeypatch.setattr(
        "api.services.application_deployment_service._shared_get_or_create_app_sg",
        lambda ec2, infra_id, vpc_id: "sg-app",
    )
    fake_session = _SessionWithEC2(_FakeEC2())

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_ecs_service(fake_session, app, env)

    container_name = ecs_cls.return_value.create_service.call_args.kwargs["container_name"]
    assert container_name == "my-app-cafebabe-task"


@pytest.mark.django_db
def test_create_ecs_service_container_name_falls_back_to_legacy_when_nothing_is_stored(deploy_fixtures, monkeypatch):
    service, _session, app, env = deploy_fixtures
    assert app.task_family is None
    monkeypatch.setattr(
        "api.services.application_deployment_service._shared_get_or_create_app_sg",
        lambda ec2, infra_id, vpc_id: "sg-app",
    )
    fake_session = _SessionWithEC2(_FakeEC2())

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_ecs_service(fake_session, app, env)

    container_name = ecs_cls.return_value.create_service.call_args.kwargs["container_name"]
    assert container_name == "my-app-task"


# ── exit_inventory + runtime_logs: read the stored family, never recompute it ───────

def test_exit_inventory_uses_the_stored_task_family():
    from api.services.exit_inventory import _task_definition_json

    app = SimpleNamespace(
        name="my-app", infrastructure_id=uuid.uuid4(), id=uuid.uuid4(),
        task_family="my-app-cafebabe-task", log_group_name=None,
    )
    rendered = _task_definition_json(app, [], {
        "image_tag": None, "commit_sha": None, "tag_source": None,
        "cpu": 0.25, "memory": 0.5, "port": 8080,
    })
    assert '"family": "my-app-cafebabe-task"' in rendered
    assert '"name": "my-app-cafebabe-task"' in rendered


def test_exit_inventory_falls_back_to_legacy_family_when_nothing_is_stored():
    from api.services.exit_inventory import _task_definition_json

    app = SimpleNamespace(
        name="my-app", infrastructure_id=uuid.uuid4(), id=uuid.uuid4(),
        task_family=None, log_group_name=None,
    )
    rendered = _task_definition_json(app, [], {
        "image_tag": None, "commit_sha": None, "tag_source": None,
        "cpu": 0.25, "memory": 0.5, "port": 8080,
    })
    assert '"family": "my-app-task"' in rendered


# ── migration 0039: backfill legacy task_family for pre-existing rows ──────────────

@pytest.mark.django_db
def test_migration_0039_backfills_only_rows_with_deploy_evidence():
    import importlib

    from django.apps import apps as django_apps

    from api.models.application import Application
    from api.models.deployment import Deployment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    migration = importlib.import_module("api.migrations.0039_backfill_legacy_task_family")

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512,
    )

    def _app(name, **extra):
        return Application.objects.create(
            id=uuid.uuid4(), user=user, infrastructure=infra, name=name,
            project_remote_url="https://github.com/x/y", project_branch="main",
            project_commit_hash="", envs={}, port=8080, alloted_cpu=256, alloted_memory=512,
            attached_database_ids=[], **extra,
        )

    with_task_def = _app("with-task-def", task_definition_arn="arn:aws:ecs:x:task-definition/x:1")
    with_service = _app("with-service", service_arn="arn:aws:ecs:x:service/cl/x")
    with_deployment_url_only = _app("with-deployment-url-only", deployment_url="http://alb.example.com/x")
    with_deployment_only = _app("with-deployment-only")
    never_deployed = _app("never-deployed")
    already_stored = _app(
        "already-stored", task_definition_arn="arn:x",
        task_family="already-stored-deadbeef-task",
    )

    Deployment.objects.create(
        application=with_deployment_only, image_tag="x", commit_sha="a" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=[], env_values_hash="h", attached_database_ids=[],
        cpu=1, memory=1, port=8080,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )

    migration.backfill_legacy_task_family(django_apps, None)

    for app in (with_task_def, with_service, with_deployment_url_only, with_deployment_only,
                never_deployed, already_stored):
        app.refresh_from_db()

    assert with_task_def.task_family == "with-task-def-task"
    assert with_service.task_family == "with-service-task"
    assert with_deployment_url_only.task_family == "with-deployment-url-only-task"
    assert with_deployment_only.task_family == "with-deployment-only-task"
    assert never_deployed.task_family is None
    assert already_stored.task_family == "already-stored-deadbeef-task"  # untouched
