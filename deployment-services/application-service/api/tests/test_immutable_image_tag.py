"""Every build produces an image that can still be identified later.

Before this, the buildspec pushed exactly one tag — `{slug}-latest` — and the task
definition referenced it. Every rebuild overwrote it, so there was never more than one
addressable image per app and rollback had nothing to roll back to. The build now also
pushes `{slug}-{sha}` and the task definition pins to that.
"""

import json
from unittest.mock import patch

import pytest
from aws.codebuild import CodeBuildClient
from aws.ecr import RETAINED_COMMIT_IMAGES, ECRClient


class _FakeClient:
    def __init__(self, builds=None):
        self._builds = builds or []
        self.lifecycle_calls = []

    def batch_get_builds(self, **kwargs):
        return {"builds": self._builds}

    def put_lifecycle_policy(self, **kwargs):
        self.lifecycle_calls.append(kwargs)
        return {}


class _FakeSession:
    def __init__(self, client):
        self._client = client

    def client(self, name, **kwargs):
        return self._client


# ── buildspec ─────────────────────────────────────────────────────────────────

@pytest.fixture
def buildspec():
    return CodeBuildClient(_FakeSession(_FakeClient()))._get_buildspec()


def test_buildspec_pushes_both_the_moving_and_the_immutable_tag(buildspec):
    assert 'docker push "$ECR_URL:$APP_NAME-latest"' in buildspec
    assert 'docker push "$ECR_URL:$APP_NAME-$RESOLVED_SHA"' in buildspec


def test_latest_is_still_pushed_so_ecs_does_not_regress(buildspec):
    """Existing task definitions reference -latest. Dropping it would break them."""
    assert 'docker tag "$APP_NAME:latest" "$ECR_URL:$APP_NAME-latest"' in buildspec


def test_sha_comes_from_the_checkout_not_the_requested_commit(buildspec):
    """On a manual deploy COMMIT_HASH is empty and the branch HEAD is what builds, so the
    tag has to describe what git actually produced."""
    assert "RESOLVED_SHA=$(git rev-parse HEAD)" in buildspec
    assert buildspec.index("git rev-parse HEAD") < buildspec.index("docker build")


def test_buildspec_exports_the_sha_so_the_caller_can_read_it_back(buildspec):
    assert "exported-variables" in buildspec
    assert "- RESOLVED_SHA" in buildspec


# ── reading the SHA back ──────────────────────────────────────────────────────

def _client_with_build(**build):
    base = {"buildStatus": "SUCCEEDED", "currentPhase": "COMPLETED", "logs": {}}
    base.update(build)
    return CodeBuildClient(_FakeSession(_FakeClient([base])))


def test_wait_for_build_returns_the_exported_sha():
    codebuild = _client_with_build(
        exportedEnvironmentVariables=[{"name": "RESOLVED_SHA", "value": "a" * 40}]
    )
    assert codebuild.wait_for_build("build-1") == "a" * 40


def test_wait_for_build_returns_none_when_the_project_predates_the_export():
    """An existing CodeBuild project still runs the old buildspec until it is updated.
    That must degrade to -latest, not fail every deploy on that infrastructure."""
    assert _client_with_build().wait_for_build("build-1") is None


def test_wait_for_build_ignores_other_exported_variables():
    codebuild = _client_with_build(
        exportedEnvironmentVariables=[{"name": "SOMETHING_ELSE", "value": "x"}]
    )
    assert codebuild.wait_for_build("build-1") is None


# ── lifecycle policy ──────────────────────────────────────────────────────────

def test_latest_is_protected_before_the_count_rule_runs():
    """Rule priority is evaluation order. A -latest image is also matched by the broad
    count rule, so if the count rule ran first it would eventually expire the tag every
    task definition still references."""
    rules = ECRClient._lifecycle_policy()["rules"]
    protect = next(r for r in rules if "latest" in r["selection"].get("tagPrefixList", []))
    count = next(r for r in rules if r["selection"].get("tagPatternList") == ["*"])
    assert protect["rulePriority"] < count["rulePriority"]


def test_retention_keeps_more_than_one_image_so_rollback_has_a_target():
    rules = ECRClient._lifecycle_policy()["rules"]
    count = next(r for r in rules if r["selection"].get("tagPatternList") == ["*"])
    assert count["selection"]["countNumber"] == RETAINED_COMMIT_IMAGES
    assert RETAINED_COMMIT_IMAGES > 1


def test_lifecycle_policy_is_valid_json_when_sent():
    fake = _FakeClient()
    assert ECRClient(_FakeSession(fake)).ensure_lifecycle_policy("launchpad-abc") is True
    sent = json.loads(fake.lifecycle_calls[0]["lifecyclePolicyText"])
    assert [r["rulePriority"] for r in sent["rules"]] == sorted(
        r["rulePriority"] for r in sent["rules"]
    )


def test_lifecycle_failure_never_fails_a_deploy():
    """Not having retention costs storage; raising here would cost the customer a deploy."""
    class _Boom:
        def put_lifecycle_policy(self, **kwargs):
            raise RuntimeError("AccessDenied")

    assert ECRClient(_FakeSession(_Boom())).ensure_lifecycle_policy("launchpad-abc") is False


@pytest.mark.parametrize("url,expected", [
    ("123456789012.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc", "launchpad-abc"),
    ("launchpad-abc", "launchpad-abc"),
    (None, None),
])
def test_repository_name_is_parsed_from_the_url(url, expected):
    assert ECRClient.repository_name_from_url(url) == expected


# ── the task definition actually pins to it ───────────────────────────────────
#
# The whole point: the image URI baked into the task definition must name a fixed image.
# Everything above is plumbing if this one regresses.

@pytest.fixture
def deploy_fixtures(schema_db):
    import uuid

    from api.mock.mock_session import MockSession
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User
    from api.services.application_deployment_service import ApplicationDeploymentService

    user = User.objects.create(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t",
    )
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512,
    )
    env = Environment.objects.create(
        id=uuid.uuid4(), infrastructure=infra,
        ecr_repository_url="123456789012.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc",
        ecs_task_execution_role_arn="arn:aws:iam::123456789012:role/exec",
    )
    app = Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    session = MockSession(region="us-east-1", account_id="123456789012", infra_id=str(infra.id))
    return ApplicationDeploymentService(), session, app, env


def test_task_definition_pins_the_image_to_the_resolved_commit(deploy_fixtures):
    service, session, app, env = deploy_fixtures
    sha = "e" * 40

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha=sha)

    image = ecs_cls.return_value.create_task_definition.call_args.kwargs["image"]
    assert image.endswith(f":my-app-{sha}")
    assert not image.endswith(":my-app-latest")


def test_task_definition_falls_back_to_latest_without_a_resolved_commit(deploy_fixtures):
    """A CodeBuild project predating the two-tag buildspec exports no SHA. The deploy must
    still work — it just is not rollback-addressable."""
    service, session, app, env = deploy_fixtures

    with patch("api.services.application_deployment_service.ECSClient") as ecs_cls:
        service._create_task_definition(session, app, env, resolved_sha=None)

    image = ecs_cls.return_value.create_task_definition.call_args.kwargs["image"]
    assert image.endswith(":my-app-latest")


def test_creating_a_task_definition_applies_ecr_retention(deploy_fixtures):
    """Pushing a second tag per build grows the repository without bound otherwise, and the
    customer pays for that storage."""
    service, session, app, env = deploy_fixtures

    with patch("api.services.application_deployment_service.ECSClient"), \
            patch.object(ECRClient, "ensure_lifecycle_policy") as lifecycle:
        service._create_task_definition(session, app, env, resolved_sha="f" * 40)

    lifecycle.assert_called_once_with("launchpad-abc")
