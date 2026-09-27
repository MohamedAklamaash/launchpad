"""F6 exit export: application-service's inventory endpoint. Owner-only authz ladder,
and the credential scan the plan requires — a seeded secret in every content type this
service can leak (Application.envs values, github_webhook_secret, User.metadata's GitHub
token, and the buildspec's would-be token) must never appear in the response."""
import json
import uuid
from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

SEEDED_ENV_SECRET = "sk-live-SEEDED-ENV-SECRET-VALUE"
SEEDED_WEBHOOK_SECRET = "whsec-SEEDED-WEBHOOK-SECRET-VALUE"
SEEDED_GITHUB_TOKEN = "ghp-SEEDED-GITHUB-TOKEN-VALUE"


@pytest.fixture
def owner(schema_db):
    from api.models.user import User

    return User.objects.create(
        id=uuid.uuid4(), email=f"o-{uuid.uuid4()}@e.io", user_name="owner",
        metadata={"github": {"token": SEEDED_GITHUB_TOKEN}},
    )


def _make_infra(owner, compute_type="ecs_fargate"):
    from api.models.infrastructure import Infrastructure

    return Infrastructure.objects.create(
        user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        compute_type=compute_type, max_cpu=4, max_memory=8, code="123456789012",
    )


def _make_app(owner, infra, **overrides):
    from api.models.application import Application

    defaults = {
        "name": "my-app", "user": owner, "infrastructure": infra,
        "project_remote_url": "https://github.com/acme/my-app", "project_branch": "main",
        "project_commit_hash": "abc123", "port": 8080,
        "envs": {"DATABASE_URL": SEEDED_ENV_SECRET, "API_KEY": "another-secret"},
        "github_webhook_secret": SEEDED_WEBHOOK_SECRET,
        "task_definition_arn": "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app-task:3",
        "service_arn": "arn:aws:ecs:us-east-1:123456789012:service/cluster/my-app",
        "target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/my-app/abc",
        "listener_rule_arn": "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener-rule/abc",
    }
    defaults.update(overrides)
    return Application.objects.create(**defaults)


def _call(infra_id, user):
    from api.views.exit_inventory import export_inventory

    req = APIRequestFactory().get(f"/api/v1/infrastructures/{infra_id}/export-inventory/")
    force_authenticate(req, user=user)
    return export_inventory(req, infra_id=infra_id)


def _as_user(u):
    return SimpleNamespace(id=str(u.id), is_authenticated=True)


@pytest.mark.django_db
def test_owner_gets_the_inventory(owner):
    infra = _make_infra(owner)
    app = _make_app(owner, infra)

    resp = _call(infra.id, _as_user(owner))

    assert resp.status_code == 200
    assert resp.data["infrastructure_id"] == str(infra.id)
    entries = resp.data["apps"]
    assert len(entries) == 1
    assert entries[0]["id"] == str(app.id)
    assert set(entries[0]["env_keys"]) == {"DATABASE_URL", "API_KEY"}


@pytest.mark.django_db
def test_stranger_gets_404(owner):
    from api.models.user import User

    infra = _make_infra(owner)
    _make_app(owner, infra)
    stranger = User.objects.create(id=uuid.uuid4(), email=f"s-{uuid.uuid4()}@e.io", user_name="stranger")

    resp = _call(infra.id, _as_user(stranger))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_invited_admin_gets_403(owner):
    from shared.enums.user_role import UserRole

    from api.models.infrastructure_user_role import InfrastructureUserRole
    from api.models.user import User

    infra = _make_infra(owner)
    _make_app(owner, infra)
    invited = User.objects.create(id=uuid.uuid4(), email=f"i-{uuid.uuid4()}@e.io", user_name="invited")
    InfrastructureUserRole.objects.create(infrastructure=infra, user=invited, role=UserRole.ADMIN)

    resp = _call(infra.id, _as_user(invited))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_repo_url_userinfo_is_scrubbed(owner):
    """A private repo pasted as https://user:TOKEN@github.com/org/repo must never carry
    the embedded token into the README, buildspec/env.example, or this response."""
    seeded_url_token = "SEEDED-URL-TOKEN"
    infra = _make_infra(owner)
    _make_app(owner, infra, project_remote_url=f"https://x:{seeded_url_token}@github.com/acme/my-app")

    resp = _call(infra.id, _as_user(owner))
    entry = resp.data["apps"][0]

    assert seeded_url_token not in json.dumps(resp.data)
    assert entry["repo_url"] == "https://github.com/acme/my-app"


@pytest.mark.django_db
def test_repo_url_query_token_is_scrubbed(owner):
    """Some hosts accept a token as a query parameter instead of userinfo — same leak,
    different shape, must be scrubbed the same way."""
    seeded_url_token = "SEEDED-QUERY-TOKEN"
    infra = _make_infra(owner)
    _make_app(owner, infra, project_remote_url=f"https://github.com/acme/my-app?access_token={seeded_url_token}")

    resp = _call(infra.id, _as_user(owner))
    entry = resp.data["apps"][0]

    assert seeded_url_token not in json.dumps(resp.data)
    assert entry["repo_url"] == "https://github.com/acme/my-app"


@pytest.mark.django_db
def test_malformed_infra_id_gets_404_not_500(owner):
    resp = _call("not-a-uuid", _as_user(owner))
    assert resp.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("compute_type", ["ecs_fargate", "eks"])
def test_no_seeded_secret_anywhere_in_the_response(owner, compute_type):
    infra = _make_infra(owner, compute_type=compute_type)
    _make_app(owner, infra)

    resp = _call(infra.id, _as_user(owner))
    blob = json.dumps(resp.data)

    assert SEEDED_ENV_SECRET not in blob
    assert SEEDED_WEBHOOK_SECRET not in blob
    assert SEEDED_GITHUB_TOKEN not in blob
    assert "another-secret" not in blob


@pytest.mark.django_db
def test_ecs_infra_gets_task_definition_json_not_k8s_manifest(owner):
    infra = _make_infra(owner, compute_type="ecs_fargate")
    _make_app(owner, infra)

    resp = _call(infra.id, _as_user(owner))
    entry = resp.data["apps"][0]
    assert entry["task_definition_json"] is not None
    assert entry["k8s_manifest_json"] is None

    task_def = json.loads(entry["task_definition_json"])
    env_names = {e["name"] for e in task_def["containerDefinitions"][0]["environment"] if e["name"] in {"DATABASE_URL", "API_KEY"}}
    assert env_names == {"DATABASE_URL", "API_KEY"}
    for env in task_def["containerDefinitions"][0]["environment"]:
        if env["name"] in {"DATABASE_URL", "API_KEY"}:
            assert env["value"] == "<redacted>"


@pytest.mark.django_db
def test_eks_infra_gets_k8s_manifest_not_task_definition(owner):
    infra = _make_infra(owner, compute_type="eks")
    _make_app(owner, infra)

    resp = _call(infra.id, _as_user(owner))
    entry = resp.data["apps"][0]
    assert entry["k8s_manifest_json"] is not None
    assert entry["task_definition_json"] is None

    documents = json.loads(entry["k8s_manifest_json"])
    secret_doc = next(d for d in documents if d["kind"] == "Secret")
    assert set(secret_doc["stringData"].keys()) == {"DATABASE_URL", "API_KEY"}
    assert all(v == "<redacted>" for v in secret_doc["stringData"].values())

    deployment_doc = next(d for d in documents if d["kind"] == "Deployment")
    app_container = deployment_doc["spec"]["template"]["spec"]["containers"][0]
    for env in app_container["env"]:
        if env["name"] in {"DATABASE_URL", "API_KEY"}:
            assert env["value"] == "<redacted>"


@pytest.mark.django_db
def test_webhook_url_present_without_exposing_the_secret(owner):
    infra = _make_infra(owner)
    _make_app(owner, infra)

    resp = _call(infra.id, _as_user(owner))
    entry = resp.data["apps"][0]
    assert entry["has_webhook_secret"] is True
    assert entry["webhook_url"].endswith(f"/api/webhooks/github/{entry['id']}")
    assert "webhook_secret" not in json.dumps(entry) or SEEDED_WEBHOOK_SECRET not in json.dumps(entry)


@pytest.mark.django_db
def test_no_webhook_secret_means_no_webhook_url(owner):
    infra = _make_infra(owner)
    _make_app(owner, infra, github_webhook_secret=None)

    resp = _call(infra.id, _as_user(owner))
    entry = resp.data["apps"][0]
    assert entry["has_webhook_secret"] is False
    assert entry["webhook_url"] is None


@pytest.mark.django_db
def test_buildspec_has_no_literal_github_token(owner):
    infra = _make_infra(owner)
    _make_app(owner, infra)

    resp = _call(infra.id, _as_user(owner))
    buildspec = resp.data["buildspec"]
    assert SEEDED_GITHUB_TOKEN not in buildspec
    assert "$GITHUB_TOKEN@" in buildspec  # still references the env var by name, never a value


@pytest.mark.django_db
def test_deployment_snapshot_is_preferred_over_current_envs(owner):
    from api.models.deployment import Deployment

    infra = _make_infra(owner)
    app = _make_app(owner, infra)
    Deployment.objects.create(
        application=app, image_tag="my-app-abc123", commit_sha="abc123",
        tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=["DATABASE_URL"], env_values_hash="deadbeef", cpu=0.5, memory=1, port=8080,
        status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
    )

    resp = _call(infra.id, _as_user(owner))
    entry = resp.data["apps"][0]
    assert entry["env_keys"] == ["DATABASE_URL"]
    assert entry["image_tag"] == "my-app-abc123"
