"""Per-app AWS resources carry launchpad:infra/launchpad:app tags (F4), and the
tag_existing_app_resources backfill command applies them to resources created before
tagging existed."""
import uuid
from unittest.mock import patch

import pytest
from aws.alb import ALBClient
from aws.codebuild import CodeBuildClient
from aws.ecs import ECSClient
from aws.tags import app_tags, as_key_value_tags, as_lower_tags, infra_tags
from django.core.management import call_command


def _tag_dict_lower(tags):
    return {t["key"]: t["value"] for t in tags}


def _tag_dict_upper(tags):
    return {t["Key"]: t["Value"] for t in tags}


# ── helper ───────────────────────────────────────────────────────────────────────

def test_app_tags_carries_both_keys():
    tags = app_tags("infra-1", "my-app")
    assert tags == {"launchpad:infra": "infra-1", "launchpad:app": "my-app"}


def test_infra_tags_omits_the_app_key():
    """The CodeBuild project/role are shared by every app on the infra; an app-level
    tag there would misattribute every other app's build minutes."""
    assert infra_tags("infra-1") == {"launchpad:infra": "infra-1"}


def test_as_lower_tags_shape():
    assert as_lower_tags({"a": "b"}) == [{"key": "a", "value": "b"}]


def test_as_key_value_tags_shape():
    assert as_key_value_tags({"a": "b"}) == [{"Key": "a", "Value": "b"}]


# ── ECS ──────────────────────────────────────────────────────────────────────────

class _FakeSession:
    def __init__(self, client):
        self._client = client

    def client(self, name, **kwargs):
        return self._client


class _FakeMeta:
    region_name = "us-east-1"


class _FakeECSClient:
    meta = _FakeMeta()

    def __init__(self, existing_service=None):
        self._existing_service = existing_service
        self.register_calls = []
        self.create_service_calls = []
        self.update_service_calls = []
        self.tag_resource_calls = []

    def register_task_definition(self, **kwargs):
        self.register_calls.append(kwargs)
        return {"taskDefinition": {"taskDefinitionArn": "arn:aws:ecs:x:task-definition/t"}}

    def describe_services(self, **kwargs):
        return {"services": [self._existing_service] if self._existing_service else []}

    def create_service(self, **kwargs):
        self.create_service_calls.append(kwargs)
        return {"service": {"serviceArn": "arn:aws:ecs:x:service/s"}}

    def update_service(self, **kwargs):
        self.update_service_calls.append(kwargs)
        return {}

    def tag_resource(self, **kwargs):
        self.tag_resource_calls.append(kwargs)
        return {}


def test_create_task_definition_carries_both_tag_keys():
    fake = _FakeECSClient()
    ecs = ECSClient(_FakeSession(fake))

    ecs.create_task_definition(
        family="my-app-task", image="img", cpu=0.25, memory=0.5, envs={},
        execution_role_arn="arn:aws:iam::1:role/x", tags=app_tags("infra-1", "my-app"),
    )

    assert _tag_dict_lower(fake.register_calls[0]["tags"]) == {
        "launchpad:infra": "infra-1", "launchpad:app": "my-app",
    }


def test_create_service_sets_managed_tags_and_propagation_and_both_tag_keys():
    fake = _FakeECSClient()
    ecs = ECSClient(_FakeSession(fake))

    ecs.create_service(
        cluster_arn="cluster", service_name="my-app-service",
        task_definition_arn="task-def", target_group_arn="tg",
        subnet_ids=["subnet-1"], security_group_ids=["sg-1"],
        container_name="my-app-task", tags=app_tags("infra-1", "my-app"),
    )

    call = fake.create_service_calls[0]
    assert call["enableECSManagedTags"] is True
    assert call["propagateTags"] == "SERVICE"
    assert _tag_dict_lower(call["tags"]) == {"launchpad:infra": "infra-1", "launchpad:app": "my-app"}


def test_redeploying_an_existing_service_tags_it_and_reasserts_propagation():
    """update_service has no `tags` parameter, so propagateTags='SERVICE' alone would
    propagate nothing from a service that predates tagging (it has no tags of its own).
    A redeploy must tag_resource the service itself before reasserting propagation for
    the claim "self-heals on redeploy" to actually be true."""
    fake = _FakeECSClient(existing_service={"status": "ACTIVE", "serviceArn": "arn:aws:ecs:x:service/s"})
    ecs = ECSClient(_FakeSession(fake))

    ecs.create_service(
        cluster_arn="cluster", service_name="my-app-service",
        task_definition_arn="task-def", target_group_arn="tg",
        subnet_ids=["subnet-1"], security_group_ids=["sg-1"],
        container_name="my-app-task", tags=app_tags("infra-1", "my-app"),
    )

    tag_call = fake.tag_resource_calls[0]
    assert tag_call["resourceArn"] == "arn:aws:ecs:x:service/s"
    assert _tag_dict_lower(tag_call["tags"]) == {"launchpad:infra": "infra-1", "launchpad:app": "my-app"}

    call = fake.update_service_calls[0]
    assert call["enableECSManagedTags"] is True
    assert call["propagateTags"] == "SERVICE"


def test_redeploying_an_existing_service_without_tags_does_not_call_tag_resource():
    fake = _FakeECSClient(existing_service={"status": "ACTIVE", "serviceArn": "arn:aws:ecs:x:service/s"})
    ecs = ECSClient(_FakeSession(fake))

    ecs.create_service(
        cluster_arn="cluster", service_name="my-app-service",
        task_definition_arn="task-def", target_group_arn="tg",
        subnet_ids=["subnet-1"], security_group_ids=["sg-1"],
        container_name="my-app-task",
    )

    assert fake.tag_resource_calls == []
    call = fake.update_service_calls[0]
    assert call["enableECSManagedTags"] is True
    assert call["propagateTags"] == "SERVICE"


# ── ALB ──────────────────────────────────────────────────────────────────────────

class _FakeALBClient:
    def __init__(self):
        self.create_target_group_calls = []
        self.create_rule_calls = []
        self.describe_target_groups_calls = 0

    def create_target_group(self, **kwargs):
        self.create_target_group_calls.append(kwargs)
        return {"TargetGroups": [{"TargetGroupArn": "arn:aws:elasticloadbalancing:x:targetgroup/tg"}]}

    def describe_tags(self, **kwargs):
        # H4: ALBClient.create_target_group verifies ownership by echoing back the tags
        # it just set — this fake mirrors that (a freshly created target group is
        # always "ours"), same as api/mock/mock_session.py's MockClient.
        tags = self.create_target_group_calls[-1].get("Tags", [])
        return {"TagDescriptions": [{"ResourceArn": kwargs["ResourceArns"][0], "Tags": tags}]}

    def describe_rules(self, **kwargs):
        return {"Rules": []}

    def create_rule(self, **kwargs):
        self.create_rule_calls.append(kwargs)
        return {"Rules": [{"RuleArn": "arn:aws:elasticloadbalancing:x:listener-rule/r"}]}


def test_create_target_group_carries_both_tag_keys():
    fake = _FakeALBClient()
    alb = ALBClient(_FakeSession(fake))

    alb.create_target_group(name="tg", vpc_id="vpc-1", tags=app_tags("infra-1", "my-app"))

    assert _tag_dict_upper(fake.create_target_group_calls[0]["Tags"]) == {
        "launchpad:infra": "infra-1", "launchpad:app": "my-app",
    }


def test_create_listener_rule_carries_both_tag_keys(monkeypatch):
    monkeypatch.setenv("ALB_RULE_PROPAGATION_DELAY", "0")
    fake = _FakeALBClient()
    alb = ALBClient(_FakeSession(fake))

    alb.create_listener_rule(
        listener_arn="listener-1", target_group_arn="tg", path_pattern="/app*", priority=1,
        tags=app_tags("infra-1", "my-app"),
    )

    assert _tag_dict_upper(fake.create_rule_calls[0]["Tags"]) == {
        "launchpad:infra": "infra-1", "launchpad:app": "my-app",
    }


# ── CodeBuild ────────────────────────────────────────────────────────────────────

class _FakeCodeBuildClient:
    def __init__(self, existing_project=False):
        self._existing_project = existing_project
        self.create_calls = []
        self.update_calls = []

    def batch_get_projects(self, **kwargs):
        return {"projects": [{"name": "p"}] if self._existing_project else []}

    def update_project(self, **kwargs):
        self.update_calls.append(kwargs)

    def create_project(self, **kwargs):
        self.create_calls.append(kwargs)


def test_ensure_project_exists_tags_a_new_project_infra_level_only():
    fake = _FakeCodeBuildClient(existing_project=False)
    codebuild = CodeBuildClient(_FakeSession(fake))

    codebuild.ensure_project_exists("p", "arn:aws:iam::1:role/x", "us-east-1", tags=infra_tags("infra-1"))

    sent = _tag_dict_lower(fake.create_calls[0]["tags"])
    assert sent == {"launchpad:infra": "infra-1"}
    assert "launchpad:app" not in sent


def test_ensure_project_exists_retags_an_existing_project():
    fake = _FakeCodeBuildClient(existing_project=True)
    codebuild = CodeBuildClient(_FakeSession(fake))

    codebuild.ensure_project_exists("p", "arn:aws:iam::1:role/x", "us-east-1", tags=infra_tags("infra-1"))

    assert _tag_dict_lower(fake.update_calls[0]["tags"]) == {"launchpad:infra": "infra-1"}


# ── backfill command ─────────────────────────────────────────────────────────────

class _FakeBackfillSession:
    def __init__(self):
        self.ecs_calls = []
        self.elbv2_calls = []
        self.codebuild = _FakeCodeBuildClient(existing_project=True)
        self.iam = _FakeIam()
        self._ecs = _BackfillECS(self.ecs_calls)
        self._elbv2 = _BackfillELBv2(self.elbv2_calls)

    def client(self, name, **kwargs):
        return {"ecs": self._ecs, "elbv2": self._elbv2, "codebuild": self.codebuild, "iam": self.iam}[name]


class _BackfillECS:
    def __init__(self, calls):
        self._calls = calls

    def tag_resource(self, **kwargs):
        self._calls.append(("tag_resource", kwargs))

    def update_service(self, **kwargs):
        self._calls.append(("update_service", kwargs))


class _BackfillELBv2:
    def __init__(self, calls):
        self._calls = calls

    def add_tags(self, **kwargs):
        self._calls.append(("add_tags", kwargs))


class _FakeIamNoSuchEntity(Exception):
    pass


class _FakeIamExceptions:
    NoSuchEntityException = _FakeIamNoSuchEntity


class _FakeIam:
    exceptions = _FakeIamExceptions()

    def __init__(self):
        self.tag_role_calls = []

    def get_role(self, **kwargs):
        return {"Role": {"Arn": "arn:aws:iam::1:role/x"}}

    def tag_role(self, **kwargs):
        self.tag_role_calls.append(kwargs)


@pytest.fixture
def backfill_fixtures(schema_db):
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512, code="123456789012", is_mock=False,
    )
    Environment.objects.create(id=uuid.uuid4(), infrastructure=infra, cluster_arn="arn:aws:ecs:x:cluster/c")
    app = Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080, alloted_cpu=256, alloted_memory=512,
        attached_database_ids=[],
        task_definition_arn="arn:aws:ecs:x:task-definition/t",
        service_arn="arn:aws:ecs:x:service/s",
        target_group_arn="arn:aws:elasticloadbalancing:x:targetgroup/tg",
        listener_rule_arn="arn:aws:elasticloadbalancing:x:listener-rule/r",
    )
    return infra, app


def test_backfill_tags_every_existing_resource(backfill_fixtures, monkeypatch):
    _infra, _app = backfill_fixtures
    session = _FakeBackfillSession()
    monkeypatch.setattr(
        "api.management.commands.tag_existing_app_resources.create_boto3_session",
        lambda infrastructure: session,
    )

    call_command("tag_existing_app_resources")

    ecs_ops = [op for op, _ in session.ecs_calls]
    assert ecs_ops.count("tag_resource") == 2  # task definition + service
    assert "update_service" in ecs_ops
    elbv2_ops = [op for op, _ in session.elbv2_calls]
    assert elbv2_ops.count("add_tags") == 2  # target group + listener rule
    assert session.iam.tag_role_calls
    assert session.codebuild.update_calls


def test_backfill_is_idempotent(backfill_fixtures, monkeypatch):
    _infra, _app = backfill_fixtures
    session = _FakeBackfillSession()
    monkeypatch.setattr(
        "api.management.commands.tag_existing_app_resources.create_boto3_session",
        lambda infrastructure: session,
    )

    call_command("tag_existing_app_resources")
    first_run_calls = len(session.ecs_calls) + len(session.elbv2_calls)
    call_command("tag_existing_app_resources")
    second_run_calls = len(session.ecs_calls) + len(session.elbv2_calls) - first_run_calls

    assert first_run_calls == second_run_calls > 0


def test_backfill_dry_run_makes_no_aws_calls(backfill_fixtures, monkeypatch):
    _infra, _app = backfill_fixtures
    session = _FakeBackfillSession()
    create_session_calls = []
    monkeypatch.setattr(
        "api.management.commands.tag_existing_app_resources.create_boto3_session",
        lambda infrastructure: create_session_calls.append(1) or session,
    )

    call_command("tag_existing_app_resources", dry_run=True)

    assert create_session_calls == []
    assert session.ecs_calls == []
    assert session.elbv2_calls == []


def test_backfill_skips_mock_infrastructures(schema_db, monkeypatch):
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512, code="000000000000", is_mock=True,
    )
    Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080, alloted_cpu=256, alloted_memory=512,
        attached_database_ids=[], task_definition_arn="arn:aws:ecs:x:task-definition/t",
    )

    def _boom(infrastructure):
        raise AssertionError("must not assume role against a mock infrastructure")

    monkeypatch.setattr(
        "api.management.commands.tag_existing_app_resources.create_boto3_session", _boom,
    )

    call_command("tag_existing_app_resources")


def test_backfill_skips_exited_infrastructures(schema_db, monkeypatch):
    """H2 security review RECOMMENDED 4: an exited infra's role may already be gone —
    this one-off backfill has no business re-touching that customer's account at all."""
    from django.utils import timezone

    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512, code="123456789012", is_mock=False,
        exited_at=timezone.now(),
    )
    Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080, alloted_cpu=256, alloted_memory=512,
        attached_database_ids=[], task_definition_arn="arn:aws:ecs:x:task-definition/t",
    )

    def _boom(infrastructure):
        raise AssertionError("must not assume role against an exited infrastructure")

    monkeypatch.setattr(
        "api.management.commands.tag_existing_app_resources.create_boto3_session", _boom,
    )

    call_command("tag_existing_app_resources")


# ── deploy call sites actually pass the tags (not just the wrappers accepting them) ──

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


def test_create_task_definition_call_site_passes_app_tags(deploy_fixtures):
    service, session, app, env = deploy_fixtures
    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha="a" * 40)

    tags = ecs_cls.return_value.create_task_definition.call_args.kwargs["tags"]
    assert tags == app_tags(app.infrastructure_id, "my-app")


def test_create_target_group_call_site_passes_app_tags(deploy_fixtures):
    """Target groups additionally carry launchpad:app-id (H4 C1) — this only asserts
    app_tags()'s own two keys are still present and correct alongside it."""
    service, session, app, env = deploy_fixtures
    with patch("api.services.application_deployment_service.ALBClient") as alb_cls:
        service._create_target_group(session, app, env)

    tags = alb_cls.return_value.create_target_group.call_args.kwargs["tags"]
    assert tags.items() >= app_tags(app.infrastructure_id, "my-app").items()


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


def test_create_ecs_service_call_site_passes_app_tags(deploy_fixtures, monkeypatch):
    service, _session, app, env = deploy_fixtures
    monkeypatch.setattr(
        "api.services.application_deployment_service._shared_get_or_create_app_sg",
        lambda ec2, infra_id, vpc_id: "sg-app",
    )
    fake_session = _SessionWithEC2(_FakeEC2())

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_ecs_service(fake_session, app, env)

    tags = ecs_cls.return_value.create_service.call_args.kwargs["tags"]
    assert tags == app_tags(app.infrastructure_id, "my-app")


def test_configure_alb_routing_call_site_passes_app_tags(deploy_fixtures):
    service, session, app, env = deploy_fixtures
    with patch("api.services.application_deployment_service.ALBClient") as alb_cls:
        alb_cls.return_value.get_listener_arn.return_value = "listener-1"
        alb_cls.return_value.get_next_priority.return_value = 1
        service._configure_alb_routing(session, app, env)

    tags = alb_cls.return_value.create_listener_rule.call_args.kwargs["tags"]
    assert tags == app_tags(app.infrastructure_id, "my-app")
