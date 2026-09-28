"""H4: AWS resource names must never be derived from a UUIDv7 prefix (CLAUDE.md) — a
short prefix repeats every ~65s platform-wide and is forceable from a row's own
`created_at`. Covers the two fixes: target group names (`target_group_name`) and ECS
log group names (`new_ecs_log_group`/`ecs_log_group_for`), plus the "never adopt a
target group that isn't ours" tag check in `ALBClient.create_target_group`.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aws.alb import ALBClient, TargetGroupOwnershipMismatch
from aws.tags import app_tags

from api.common.naming import (
    MAX_TARGET_GROUP_NAME_LENGTH,
    ecs_log_group,
    ecs_log_group_for,
    new_ecs_log_group,
    target_group_name,
)

# Two UUIDs sharing the old, vulnerable [:8] prefix but differing right after it — the
# exact "two infras created within the same ~65s window" collision CLAUDE.md warns about.
_SHARED_PREFIX_INFRA_A = uuid.UUID("019f2c2b-9b19-71a9-8f3e-cd81d13effb9")
_SHARED_PREFIX_INFRA_B = uuid.UUID("019f2c2b-1111-71a9-8f3e-cd81d13effb9")


def _fake_app(*, infrastructure_id, application_id, name="my-app", task_definition_arn=None,
              log_group_name=None):
    return SimpleNamespace(
        infrastructure_id=infrastructure_id, id=application_id, name=name,
        task_definition_arn=task_definition_arn, log_group_name=log_group_name,
    )


# ── target_group_name ───────────────────────────────────────────────────────────────

def test_old_formula_collides_on_a_shared_uuid_prefix():
    """Documents the regression this fixes: the pre-H4 formula
    (`f"{slug}-{infra_id[:8]}-tg"`) collides for two different infras whose ids merely
    share their first 8 hex characters."""
    old_name_a = f"my-app-{str(_SHARED_PREFIX_INFRA_A)[:8]}-tg"[:32]
    old_name_b = f"my-app-{str(_SHARED_PREFIX_INFRA_B)[:8]}-tg"[:32]
    assert old_name_a == old_name_b


def test_target_group_name_distinct_for_two_infras_sharing_a_uuid_prefix():
    app_id = uuid.uuid4()
    name_a = target_group_name(_fake_app(infrastructure_id=_SHARED_PREFIX_INFRA_A, application_id=app_id))
    name_b = target_group_name(_fake_app(infrastructure_id=_SHARED_PREFIX_INFRA_B, application_id=app_id))
    assert name_a != name_b


def test_target_group_name_distinct_for_two_applications_on_the_same_infra():
    """A recreated app (same slug, same infra, new id) must not land on its
    predecessor's name — an infra-only discriminator would miss this."""
    infra_id = uuid.uuid4()
    name_1 = target_group_name(_fake_app(infrastructure_id=infra_id, application_id=uuid.uuid4()))
    name_2 = target_group_name(_fake_app(infrastructure_id=infra_id, application_id=uuid.uuid4()))
    assert name_1 != name_2


def test_target_group_name_is_deterministic():
    infra_id, app_id = uuid.uuid4(), uuid.uuid4()
    app = _fake_app(infrastructure_id=infra_id, application_id=app_id)
    assert target_group_name(app) == target_group_name(app)


def test_target_group_name_never_exceeds_the_alb_limit_and_keeps_the_hash_intact():
    long_name = "a-very-long-application-name-that-would-have-eaten-the-old-suffix"
    app = _fake_app(infrastructure_id=uuid.uuid4(), application_id=uuid.uuid4(), name=long_name)
    tg_name = target_group_name(app)
    assert len(tg_name) <= MAX_TARGET_GROUP_NAME_LENGTH
    assert tg_name.endswith("-tg")
    # The discriminator hash (8 hex chars) must survive truncation in full — only the
    # slug may be cut, never the "-{hash}-tg" suffix (the pre-H4 bug truncated the
    # whole string, which could eat into or remove the suffix for a long name).
    digest = tg_name[-11:-3]
    assert len(digest) == 8
    assert all(c in "0123456789abcdef" for c in digest)


def test_target_group_name_strips_characters_alb_rejects():
    app = _fake_app(infrastructure_id=uuid.uuid4(), application_id=uuid.uuid4(), name="my.weird_app")
    tg_name = target_group_name(app)
    assert set(tg_name) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")


# ── log group naming ─────────────────────────────────────────────────────────────────

def test_new_ecs_log_group_distinct_for_two_infras_sharing_a_uuid_prefix():
    app_id = uuid.uuid4()
    group_a = new_ecs_log_group(_fake_app(infrastructure_id=_SHARED_PREFIX_INFRA_A, application_id=app_id))
    group_b = new_ecs_log_group(_fake_app(infrastructure_id=_SHARED_PREFIX_INFRA_B, application_id=app_id))
    assert group_a != group_b


def test_ecs_log_group_for_falls_back_to_legacy_when_nothing_is_stored():
    app = _fake_app(infrastructure_id=uuid.uuid4(), application_id=uuid.uuid4(), name="my-app")
    assert ecs_log_group_for(app) == ecs_log_group("my-app")


def test_ecs_log_group_for_prefers_the_stored_name():
    stored = "/ecs/my-app-deadbeef-task"
    app = _fake_app(
        infrastructure_id=uuid.uuid4(), application_id=uuid.uuid4(), name="my-app",
        log_group_name=stored,
    )
    assert ecs_log_group_for(app) == stored


# ── _create_target_group call site ──────────────────────────────────────────────────

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
def test_create_target_group_call_site_uses_the_hashed_name(deploy_fixtures):
    service, session, app, env = deploy_fixtures
    with patch("api.services.application_deployment_service.ALBClient") as alb_cls:
        service._create_target_group(session, app, env)

    name = alb_cls.return_value.create_target_group.call_args.kwargs["name"]
    assert name == target_group_name(app)


@pytest.mark.django_db
def test_create_target_group_reuses_a_valid_stored_arn_without_calling_alb_create(deploy_fixtures):
    """Existing apps keep their stored target_group_arn — no rename of a live
    resource, and no new AWS call to derive a name for it."""
    service, session, app, env = deploy_fixtures
    app.target_group_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/legacy/abc"
    app.save(update_fields=["target_group_arn"])

    with patch("api.services.application_deployment_service.ALBClient") as alb_cls:
        alb_cls.return_value.client.describe_target_groups.return_value = {
            "TargetGroups": [{"VpcId": env.vpc_id}]
        }
        result = service._create_target_group(session, app, env)

    alb_cls.return_value.create_target_group.assert_not_called()
    assert result == app.target_group_arn


# ── ALBClient.create_target_group: never adopt a foreign target group ──────────────

def _albclient(session):
    return ALBClient(session)


def test_foreign_target_group_adoption_raises_on_tag_mismatch():
    from api.mock.mock_session import MockSession

    session = MockSession(region="us-east-1", account_id="123456789012", infra_id="infra-a")
    alb = _albclient(session)

    # A target group already exists under this exact name, owned by a different
    # app/infra (however that became possible — a hash collision, a manual AWS console
    # action, an orphan from a different naming scheme).
    alb.create_target_group(
        name="shared-name-tg", vpc_id="vpc-1", tags=app_tags("infra-OTHER", "other-app"),
    )

    with pytest.raises(TargetGroupOwnershipMismatch):
        alb.create_target_group(
            name="shared-name-tg", vpc_id="vpc-1", tags=app_tags("infra-a", "my-app"),
        )


def test_matching_tags_adoption_succeeds_and_is_self_healing():
    """The same app/infra re-creating its own (still-present) target group — e.g. an
    environment rebuild that didn't clear application.target_group_arn — adopts it back
    rather than failing, because the tags actually match."""
    from api.mock.mock_session import MockSession

    session = MockSession(region="us-east-1", account_id="123456789012", infra_id="infra-a")
    alb = _albclient(session)
    tags = app_tags("infra-a", "my-app")

    first_arn = alb.create_target_group(name="my-app-tg", vpc_id="vpc-1", tags=tags)
    second_arn = alb.create_target_group(name="my-app-tg", vpc_id="vpc-1", tags=tags)

    assert first_arn == second_arn


class _FakeElbv2SettingsMatchAdoption:
    """Models the AWS behaviour the task description is actually about: when a target
    group of the requested name already exists with MATCHING settings, CreateTargetGroup
    does not raise DuplicateTargetGroupNameException at all — it returns a plain success
    carrying the pre-existing (foreign) target group's ARN. `api/mock/mock_session.py`'s
    MockClient always raises Duplicate on a repeat name instead (the differing-settings
    variant); this fake exists to cover the other one, since which branch fires decides
    whether TargetGroupOwnershipMismatch has anything to check at all."""

    def __init__(self, foreign_arn: str, foreign_tags: dict):
        self._foreign_arn = foreign_arn
        self._foreign_tags = foreign_tags

    def create_target_group(self, **kwargs):
        return {"TargetGroups": [{"TargetGroupArn": self._foreign_arn}]}

    def describe_tags(self, **kwargs):
        return {"TagDescriptions": [{
            "ResourceArn": kwargs["ResourceArns"][0],
            "Tags": [{"Key": k, "Value": v} for k, v in self._foreign_tags.items()],
        }]}


class _FakeSessionFor:
    def __init__(self, client):
        self._client = client

    def client(self, name):
        return self._client


def test_settings_match_adoption_without_any_exception_still_raises_on_tag_mismatch():
    """The scenario H4 names explicitly: no DuplicateTargetGroupNameException at all —
    just a quiet success that hands back someone else's target group ARN."""
    elbv2 = _FakeElbv2SettingsMatchAdoption(
        foreign_arn="arn:aws:elasticloadbalancing:x:targetgroup/foreign/1",
        foreign_tags=app_tags("infra-OTHER", "other-app"),
    )
    alb = ALBClient(_FakeSessionFor(elbv2))

    with pytest.raises(TargetGroupOwnershipMismatch):
        alb.create_target_group(name="tg", vpc_id="vpc-1", tags=app_tags("infra-a", "my-app"))


def test_settings_match_adoption_succeeds_when_tags_actually_match():
    elbv2 = _FakeElbv2SettingsMatchAdoption(
        foreign_arn="arn:aws:elasticloadbalancing:x:targetgroup/mine/1",
        foreign_tags=app_tags("infra-a", "my-app"),
    )
    alb = ALBClient(_FakeSessionFor(elbv2))

    arn = alb.create_target_group(name="tg", vpc_id="vpc-1", tags=app_tags("infra-a", "my-app"))
    assert arn == "arn:aws:elasticloadbalancing:x:targetgroup/mine/1"


class _FakeElbv2EventuallyConsistentTags:
    """C3: DescribeTags right after CreateTargetGroup(Tags=...) — models a real
    account's tags not being immediately readable, resolving after a couple of
    retries."""

    def __init__(self, arn: str, tags: dict, consistent_after: int):
        self._arn = arn
        self._tags = tags
        self._consistent_after = consistent_after
        self.describe_tags_calls = 0

    def create_target_group(self, **kwargs):
        return {"TargetGroups": [{"TargetGroupArn": self._arn}]}

    def describe_tags(self, **kwargs):
        self.describe_tags_calls += 1
        tags = self._tags if self.describe_tags_calls > self._consistent_after else {}
        return {"TagDescriptions": [{
            "ResourceArn": kwargs["ResourceArns"][0],
            "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
        }]}


def test_ownership_check_retries_before_failing_on_delayed_tag_propagation(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    tags = app_tags("infra-a", "my-app")
    elbv2 = _FakeElbv2EventuallyConsistentTags(
        arn="arn:aws:elasticloadbalancing:x:targetgroup/mine/1", tags=tags, consistent_after=1,
    )
    alb = ALBClient(_FakeSessionFor(elbv2))

    arn = alb.create_target_group(name="tg", vpc_id="vpc-1", tags=tags)

    assert arn == "arn:aws:elasticloadbalancing:x:targetgroup/mine/1"
    assert elbv2.describe_tags_calls == 2


def test_ownership_check_still_fails_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    elbv2 = _FakeElbv2EventuallyConsistentTags(
        arn="arn:aws:elasticloadbalancing:x:targetgroup/foreign/1",
        tags=app_tags("infra-OTHER", "other-app"), consistent_after=0,
    )
    alb = ALBClient(_FakeSessionFor(elbv2))

    with pytest.raises(TargetGroupOwnershipMismatch):
        alb.create_target_group(name="tg", vpc_id="vpc-1", tags=app_tags("infra-a", "my-app"))
    assert elbv2.describe_tags_calls == 3  # 1 initial + 2 retries, then give up


def test_adoption_check_is_skipped_when_caller_passes_no_tags():
    """Callers that never pass tags in the first place get the pre-H4 behaviour
    (reuse-by-name-and-VPC only) — the ownership check has nothing to compare against."""
    from api.mock.mock_session import MockSession

    session = MockSession(region="us-east-1", account_id="123456789012", infra_id="infra-a")
    alb = _albclient(session)

    first_arn = alb.create_target_group(name="untagged-tg", vpc_id="vpc-1")
    second_arn = alb.create_target_group(name="untagged-tg", vpc_id="vpc-1")

    assert first_arn == second_arn


# ── ECS task-definition log group: new vs. legacy vs. stored ───────────────────────

@pytest.mark.django_db
def test_first_deploy_of_a_new_app_persists_a_hashed_log_group(deploy_fixtures):
    service, session, app, env = deploy_fixtures
    assert app.log_group_name is None
    assert app.task_definition_arn is None

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    expected = new_ecs_log_group(app)
    app.refresh_from_db()
    assert app.log_group_name == expected
    assert ecs_cls.return_value.create_task_definition.call_args.kwargs["log_group"] == expected


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
def test_redeploy_of_a_pre_h4_app_keeps_the_legacy_log_group(deploy_fixtures):
    """An app that already deployed successfully before `log_group_name` existed must
    not have its logs silently moved to a new group on its next deploy."""
    service, session, app, env = deploy_fixtures
    app.task_definition_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app-task:3"
    app.save(update_fields=["task_definition_arn"])
    _record_succeeded_deployment(app)

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    legacy = ecs_log_group("my-app")
    app.refresh_from_db()
    assert app.log_group_name == legacy
    assert ecs_cls.return_value.create_task_definition.call_args.kwargs["log_group"] == legacy


@pytest.mark.django_db
def test_retry_after_failure_does_not_switch_a_previously_deployed_apps_log_group(deploy_fixtures):
    """H4 review: ApplicationRetryDeployView nulls task_definition_arn (among other
    ARNs) on a live Application row before re-queuing a failed deploy, without deleting
    the row. An app that succeeded before must keep its legacy log group across that
    reset — task_definition_arn being null must not read as "never deployed"."""
    service, session, app, env = deploy_fixtures
    _record_succeeded_deployment(app)
    assert app.task_definition_arn is None  # simulates the retry view's reset

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    legacy = ecs_log_group("my-app")
    app.refresh_from_db()
    assert app.log_group_name == legacy
    assert ecs_cls.return_value.create_task_definition.call_args.kwargs["log_group"] == legacy


@pytest.mark.django_db
def test_retry_queued_before_migration_runs_still_keeps_legacy_log_group_via_deployment_url(deploy_fixtures):
    """R1 (second review pass): the narrow race the migration's own filter can miss —
    a pre-F3 app (no Deployment rows) whose retry is queued in the window between
    ApplicationRetryDeployView resetting task_definition_arn/service_arn and
    migration 0035 running, so the migration's candidate filter doesn't catch it
    either. deployment_url — set only by a prior success, never reset by the retry
    view — is what closes this: _has_succeeded_before must return True from
    deployment_url alone, with neither ARN field nor any Deployment row present."""
    service, session, app, env = deploy_fixtures
    app.deployment_url = "http://alb.example.com/my-app"
    app.save(update_fields=["deployment_url"])
    assert app.task_definition_arn is None
    assert app.service_arn is None

    from api.models.deployment import Deployment
    assert not Deployment.objects.filter(application=app).exists()

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    legacy = ecs_log_group("my-app")
    app.refresh_from_db()
    assert app.log_group_name == legacy
    assert ecs_cls.return_value.create_task_definition.call_args.kwargs["log_group"] == legacy


@pytest.mark.django_db
def test_subsequent_deploy_reuses_the_already_stored_log_group(deploy_fixtures):
    service, session, app, env = deploy_fixtures
    stored = "/ecs/my-app-cafebabe-task"
    app.log_group_name = stored
    app.task_definition_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app-task:1"
    app.save(update_fields=["log_group_name", "task_definition_arn"])

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    app.refresh_from_db()
    assert app.log_group_name == stored
    assert ecs_cls.return_value.create_task_definition.call_args.kwargs["log_group"] == stored


@pytest.mark.django_db
def test_pre_f3_app_with_task_definition_arn_but_no_deployment_history_keeps_legacy_log_group(deploy_fixtures):
    """R1 (post-review): an app deployed before Deployment history existed (migration
    0029 added that table with no backfill) has a task_definition_arn but zero
    Deployment rows. task_definition_arn alone must be enough to keep it on the
    legacy log group — it must not need Deployment history it can never have."""
    service, session, app, env = deploy_fixtures
    app.task_definition_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app-task:1"
    app.save(update_fields=["task_definition_arn"])

    from api.models.deployment import Deployment
    assert not Deployment.objects.filter(application=app).exists()

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    legacy = ecs_log_group("my-app")
    app.refresh_from_db()
    assert app.log_group_name == legacy
    assert ecs_cls.return_value.create_task_definition.call_args.kwargs["log_group"] == legacy


# ── migration 0035: backfill legacy log_group_name for pre-existing rows ────────────

@pytest.mark.django_db
def test_migration_0035_backfills_only_rows_with_deploy_evidence():
    import importlib

    from django.apps import apps as django_apps

    from api.models.application import Application
    from api.models.deployment import Deployment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    migration = importlib.import_module("api.migrations.0035_backfill_legacy_log_group_name")

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
        log_group_name="/ecs/already-stored-deadbeef-task",
    )

    Deployment.objects.create(
        application=with_deployment_only, image_tag="x", commit_sha="a" * 40,
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=[], env_values_hash="h", attached_database_ids=[],
        cpu=1, memory=1, port=8080,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )

    migration.backfill_legacy_log_group_name(django_apps, None)

    for app in (with_task_def, with_service, with_deployment_url_only, with_deployment_only,
                never_deployed, already_stored):
        app.refresh_from_db()

    assert with_task_def.log_group_name == "/ecs/with-task-def-task"
    assert with_service.log_group_name == "/ecs/with-service-task"
    assert with_deployment_url_only.log_group_name == "/ecs/with-deployment-url-only-task"
    assert with_deployment_only.log_group_name == "/ecs/with-deployment-only-task"
    assert never_deployed.log_group_name is None
    assert already_stored.log_group_name == "/ecs/already-stored-deadbeef-task"  # untouched


# ── C1: launchpad:app-id distinguishes a recreated app from its predecessor ─────────

def test_target_group_tags_includes_the_full_application_id():
    from aws.tags import TAG_APP_ID_KEY, target_group_tags

    app_id = uuid.uuid4()
    tags = target_group_tags("infra-1", "my-app", app_id)
    assert tags["launchpad:infra"] == "infra-1"
    assert tags["launchpad:app"] == "my-app"
    assert tags[TAG_APP_ID_KEY] == str(app_id)


@pytest.mark.django_db
def test_create_target_group_call_site_tags_include_the_application_id(deploy_fixtures):
    from aws.tags import TAG_APP_ID_KEY

    service, session, app, env = deploy_fixtures
    with patch("api.services.application_deployment_service.ALBClient") as alb_cls:
        service._create_target_group(session, app, env)

    tags = alb_cls.return_value.create_target_group.call_args.kwargs["tags"]
    assert tags[TAG_APP_ID_KEY] == str(app.id)


# ── C2: deterministic (not wall-clock) wrong-VPC fallback naming ───────────────────

def test_vpc_discriminated_name_is_deterministic_not_time_based():
    from api.mock.mock_session import MockSession

    session = MockSession(region="us-east-1", account_id="123456789012", infra_id="infra-a")
    alb = _albclient(session)
    tags = app_tags("infra-a", "my-app")

    name_1 = alb._vpc_discriminated_name("my-app-a1b2c3d4-tg", "vpc-1", tags)
    name_2 = alb._vpc_discriminated_name("my-app-a1b2c3d4-tg", "vpc-1", tags)
    assert name_1 == name_2


def test_vpc_discriminated_name_stays_within_the_alb_limit_and_charset():
    session_tags = app_tags("infra-a-very-long-identifier", "a-very-long-application-name")
    from api.mock.mock_session import MockSession

    session = MockSession(region="us-east-1", account_id="123456789012", infra_id="infra-a")
    alb = _albclient(session)

    name = alb._vpc_discriminated_name("a-very-long-application-name-hash1234-tg", "vpc-1", session_tags)
    assert len(name) <= 32
    assert set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")
    assert name.endswith("-tg")


# ── C4: the mismatch error names the target group and gives guidance ───────────────

def test_ownership_mismatch_message_names_the_target_group_with_guidance():
    exc = TargetGroupOwnershipMismatch("arn:aws:elasticloadbalancing:x:targetgroup/tg/1", "my-tg")
    message = str(exc)
    assert "my-tg" in message
    assert "arn:aws:elasticloadbalancing:x:targetgroup/tg/1" in message
    assert "delete" in message.lower()
    assert "rename" in message.lower()


def test_ownership_mismatch_message_survives_sanitize_deploy_error():
    from shared.errors.deploy_errors import sanitize_deploy_error

    exc = TargetGroupOwnershipMismatch("arn:aws:elasticloadbalancing:x:targetgroup/tg/1", "my-tg")
    sanitized = sanitize_deploy_error(exc)
    assert "my-tg" in sanitized
    assert "delete" in sanitized.lower()
