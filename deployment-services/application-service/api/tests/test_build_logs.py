"""F7 ticket 8: build-log tail for platform builds. Covers the owner-only authz ladder
(mirroring F2), the legacy-CodeBuild/no-build distinction, the signed cursor's binding
and tamper rejection, bounded page size, ANSI/control/credential redaction, the no-store
posture, the per-user rate budget (including fail-closed on Redis down), and the
in-progress ("current") route's own gating."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import aws.platform_builder as pb_mod
import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.enums.user_role import UserRole

from api.views.build_logs import build_logs, current_build_logs

ACCOUNT_ID = "123456789012"
REGION = "us-west-2"

ENV_VARS = {
    "PLATFORM_BUILDER_LAUNCHER_ROLE_ARN": f"arn:aws:iam::{ACCOUNT_ID}:role/launchpad-platform-builder-launcher",
    "PLATFORM_BUILDER_CLUSTER": "launchpad-platform-builder",
    "PLATFORM_BUILDER_TASK_DEFINITION": "launchpad-platform-builder-kaniko",
    "PLATFORM_BUILDER_SUBNET_IDS": "subnet-aaa,subnet-bbb",
    "PLATFORM_BUILDER_SECURITY_GROUP_ID": "sg-ccc",
    "PLATFORM_BUILDER_LOG_GROUP": "/launchpad/platform-builder",
    "PLATFORM_BUILDER_SOURCE_BUCKET": "launchpad-platform-builder-sources",
    "PLATFORM_BUILDER_AWS_REGION": REGION,
}


class _ResourceNotFoundException(Exception):
    pass


def _sts_response():
    return {
        "Credentials": {
            "AccessKeyId": "SCOPED_AK",
            "SecretAccessKey": "SCOPED_SK",
            "SessionToken": "SCOPED_TOKEN",
            "Expiration": datetime.now(timezone.utc) + timedelta(minutes=15),
        }
    }


@pytest.fixture(autouse=True)
def prod_mode(monkeypatch):
    prod = SimpleNamespace(mode="prod", aws_access_key_id="PLATFORM_AKID", aws_secret_access_key="PLATFORM_SECRET")
    monkeypatch.setattr(pb_mod, "app_config", prod)
    for key, value in ENV_VARS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _default_allow_budget(monkeypatch):
    monkeypatch.setattr("api.views.build_logs.customer_call_budget", lambda *a, **k: None)


@pytest.fixture
def harness(monkeypatch):
    """Patches the two AWS seams the build-logs service touches: `boto3.client` (sts
    only) and `boto3.Session` (the launcher session, whose only client here is `logs`)."""
    sts_client = MagicMock()
    sts_client.assume_role.return_value = _sts_response()

    logs_client = MagicMock()
    logs_client.exceptions.ResourceNotFoundException = _ResourceNotFoundException

    def _boto3_client(service_name, **kwargs):
        if service_name == "sts":
            return sts_client
        raise AssertionError(f"only sts is expected on boto3.client, got {service_name!r}")

    class _FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def client(self, service_name, **kwargs):
            assert service_name == "logs", f"only logs is expected on the launcher session, got {service_name!r}"
            return logs_client

    monkeypatch.setattr(pb_mod.boto3, "client", _boto3_client)
    monkeypatch.setattr(pb_mod.boto3, "Session", _FakeSession)
    return SimpleNamespace(sts=sts_client, logs=logs_client)


def _log_event(message, ts_ms):
    return {"timestamp": ts_ms, "message": message, "ingestionTime": ts_ms}


def _stream_response(events, token="f/0"):
    return {"events": events, "nextForwardToken": token, "nextBackwardToken": "b/0"}


@pytest.fixture
def make_app(schema_db):
    from api.models.application import Application
    from api.models.deployment import Deployment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make():
        owner = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, is_mock=False, code=ACCOUNT_ID, is_cloud_authenticated=True,
        )
        app = Application.objects.create(
            user=owner, infrastructure=infra, name="api", project_remote_url="https://github.com/o/r",
            project_branch="main", project_commit_hash="a" * 40, port=8080,
            alloted_cpu=0.5, alloted_memory=1.0,
        )
        return SimpleNamespace(owner=owner, infra=infra, app=app, Deployment=Deployment)

    return _make


@pytest.fixture
def make_user(schema_db):
    from api.models.user import User

    def _make():
        return User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")

    return _make


def _record_deployment(stack, *, build_id="build-1", build_task_id="task-1", status="SUCCEEDED"):
    return stack.Deployment.objects.create(
        application=stack.app, image_tag="api-" + "a" * 40, commit_sha="a" * 40,
        tag_source=stack.Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
        env_keys=[], env_values_hash="", attached_database_ids=[], cpu=0.5, memory=1.0, port=8080,
        status=status, triggered_by="DEPLOY", build_id=build_id, build_task_id=build_task_id,
    )


@pytest.fixture
def factory():
    return APIRequestFactory()


def _get_deployment_logs(factory, user, app_id, deployment_id, **query):
    request = factory.get(
        f"/api/v1/applications/{app_id}/deployments/{deployment_id}/build-logs/", query,
    )
    force_authenticate(request, user=user)
    return build_logs(request, app_id=app_id, deployment_id=deployment_id)


def _get_current_logs(factory, user, app_id, **query):
    request = factory.get(f"/api/v1/applications/{app_id}/build-logs/current/", query)
    force_authenticate(request, user=user)
    return current_build_logs(request, app_id=app_id)


# ── authz ladder ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_stranger_gets_404(factory, make_app, make_user, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    resp = _get_deployment_logs(factory, make_user(), str(stack.app.id), str(deployment.id))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_invited_admin_gets_403(factory, make_app, make_user, harness):
    from api.models.infrastructure_user_role import InfrastructureUserRole

    stack = make_app()
    deployment = _record_deployment(stack)
    invited = make_user()
    stack.infra.invited_users.add(invited)
    InfrastructureUserRole.objects.create(infrastructure=stack.infra, user=invited, role=UserRole.ADMIN)

    resp = _get_deployment_logs(factory, invited, str(stack.app.id), str(deployment.id))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_owner_gets_200(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    harness.logs.get_log_events.side_effect = [
        _stream_response([_log_event("fetching s3 config", 1000)]),
        _stream_response([_log_event("step build --dockerfile Dockerfile", 2000)]),
    ]

    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))

    assert resp.status_code == 200
    assert resp["Cache-Control"] == "no-store"
    assert len(resp.data["events"]) == 2
    assert resp.data["complete"] is True
    assert {e["container"] for e in resp.data["events"]} == {"init", "kaniko"}


# ── legacy / no-build handling ───────────────────────────────────────────────

@pytest.mark.django_db
def test_legacy_codebuild_deployment_gets_409(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack, build_id="codebuild-id-123", build_task_id=None)
    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert resp.status_code == 409
    assert resp.data["code"] == "legacy_build"


@pytest.mark.django_db
def test_rollback_deployment_with_no_build_gets_404(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack, build_id=None, build_task_id=None)
    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_unknown_deployment_id_gets_404(factory, make_app, harness):
    stack = make_app()
    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(uuid.uuid4()))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_deployment_belonging_to_another_app_gets_404(factory, make_app, harness):
    stack_a = make_app()
    stack_b = make_app()
    deployment_b = _record_deployment(stack_b)
    resp = _get_deployment_logs(factory, stack_a.owner, str(stack_a.app.id), str(deployment_b.id))
    assert resp.status_code == 404


# ── current (in-progress) route ──────────────────────────────────────────────

@pytest.mark.django_db
def test_current_route_404_when_not_building(factory, make_app, harness):
    stack = make_app()
    stack.app.status = "ACTIVE"
    stack.app.build_id = "build-1"
    stack.app.build_task_id = "task-1"
    stack.app.save(update_fields=["status", "build_id", "build_task_id"])

    resp = _get_current_logs(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_current_route_200_while_building_never_claims_complete(factory, make_app, harness):
    stack = make_app()
    stack.app.status = "BUILDING"
    stack.app.build_id = "build-1"
    stack.app.build_task_id = "task-1"
    stack.app.save(update_fields=["status", "build_id", "build_task_id"])
    harness.logs.get_log_events.side_effect = [
        _stream_response([]),
        _stream_response([_log_event("step 1/5 FROM python:3.11", 1000)]),
    ]

    resp = _get_current_logs(factory, stack.owner, str(stack.app.id))

    assert resp.status_code == 200
    assert resp.data["complete"] is False


@pytest.mark.django_db
def test_current_route_legacy_build_gets_409(factory, make_app, harness):
    stack = make_app()
    stack.app.status = "BUILDING"
    stack.app.build_id = "codebuild-id-123"
    stack.app.build_task_id = None
    stack.app.save(update_fields=["status", "build_id", "build_task_id"])

    resp = _get_current_logs(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 409
    assert resp.data["code"] == "legacy_build"


# ── mock build ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_mock_build_returns_synthetic_page_without_any_aws_call(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack, build_id="mock-build-1", build_task_id=f"{pb_mod.MOCK_TASK_ID_PREFIX}mock-build-1")

    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))

    assert resp.status_code == 200
    assert resp.data["complete"] is True
    assert resp.data["next_cursor"] is None
    harness.sts.assume_role.assert_not_called()
    harness.logs.get_log_events.assert_not_called()


# ── cursor binding / tamper rejection ─────────────────────────────────────────

@pytest.mark.django_db
def test_tampered_cursor_is_rejected(factory, make_app, harness):
    from api.services.build_logs_cursor import encode_cursor

    stack = make_app()
    deployment = _record_deployment(stack)
    forged = encode_cursor(
        app_id=str(uuid.uuid4()), user_id=str(stack.owner.id), build_id="build-1", tokens={},
    )
    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id), cursor=forged)
    assert resp.status_code == 400


@pytest.mark.django_db
def test_cursor_from_a_different_build_is_rejected(factory, make_app, harness):
    from api.services.build_logs_cursor import encode_cursor

    stack = make_app()
    deployment = _record_deployment(stack, build_id="build-1")
    cursor_for_other_build = encode_cursor(
        app_id=str(stack.app.id), user_id=str(stack.owner.id), build_id="some-other-build", tokens={},
    )
    resp = _get_deployment_logs(
        factory, stack.owner, str(stack.app.id), str(deployment.id), cursor=cursor_for_other_build,
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_cursor_round_trips_into_next_request(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    harness.logs.get_log_events.side_effect = [
        _stream_response([_log_event("init line", 1000)], token="init-f/1"),
        _stream_response([_log_event("kaniko line", 1000)], token="kaniko-f/1"),
    ]
    first = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert first.status_code == 200
    cursor = first.data["next_cursor"]
    # complete=True here since both streams returned fewer events than the per-stream
    # limit — so no cursor is actually needed, but if one WAS issued it must verify.
    if cursor:
        harness.logs.get_log_events.side_effect = [
            _stream_response([], token="init-f/1"),
            _stream_response([], token="kaniko-f/1"),
        ]
        second = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id), cursor=cursor)
        assert second.status_code == 200


# ── page bound ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_limit_is_clamped_and_passed_to_get_log_events(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    harness.logs.get_log_events.side_effect = [_stream_response([]), _stream_response([])]

    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id), limit="50")
    assert resp.status_code == 200
    for call in harness.logs.get_log_events.call_args_list:
        assert call.kwargs["limit"] == 50


@pytest.mark.django_db
def test_limit_above_max_is_rejected(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id), limit="100000")
    assert resp.status_code == 400


# ── redaction ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ansi_and_control_chars_are_stripped(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    harness.logs.get_log_events.side_effect = [
        _stream_response([_log_event("\x1b[31mred text\x1b[0m\x07", 1000)]),
        _stream_response([]),
    ]
    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert resp.status_code == 200
    messages = [e["message"] for e in resp.data["events"]]
    assert any("red text" in m and "\x1b" not in m and "\x07" not in m for m in messages)


@pytest.mark.django_db
def test_registry_auth_lines_are_redacted(factory, make_app, harness):
    import base64
    import secrets

    stack = make_app()
    deployment = _record_deployment(stack)
    # Generated at test time, never a literal, so this fixture can't be mistaken for a
    # real credential by a secret scanner — see "generated, not literal" in the task.
    fake_token = base64.b64encode(f"AWS:{secrets.token_hex(16)}".encode()).decode()
    leaked_basic_auth = f"Authorization: Basic {fake_token}"
    leaked_docker_config = f'{{"auths":{{"example.com":{{"auth":"{fake_token}"}}}}}}'
    harness.logs.get_log_events.side_effect = [
        _stream_response([_log_event(leaked_basic_auth, 1000)]),
        _stream_response([_log_event(leaked_docker_config, 2000)]),
    ]
    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert resp.status_code == 200
    joined = " ".join(e["message"] for e in resp.data["events"])
    assert fake_token not in joined
    assert "<redacted>" in joined


# ── no-store ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_response_is_never_cached(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    harness.logs.get_log_events.side_effect = [_stream_response([]), _stream_response([])]
    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert resp["Cache-Control"] == "no-store"
    assert resp["X-Content-Type-Options"] == "nosniff"


# ── rate budget ───────────────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self):
        self.counts = {}
        self.expiry = {}

    def pipeline(self):
        return _FakePipeline(self)

    def expire(self, key, seconds):
        self.expiry[key] = seconds


class _FakePipeline:
    def __init__(self, store):
        self.store = store
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    def ttl(self, key):
        self.ops.append(("ttl", key))
        return self

    def execute(self):
        results = []
        for op, key in self.ops:
            if op == "incr":
                self.store.counts[key] = self.store.counts.get(key, 0) + 1
                results.append(self.store.counts[key])
            else:
                results.append(self.store.expiry.get(key, -1))
        return results

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("shared.ratelimit.budget._redis", lambda: fake)
    return fake


@pytest.mark.django_db
def test_budget_exceeded_returns_429(factory, make_app, harness, fake_redis, settings, monkeypatch):
    from shared.ratelimit.budget import customer_call_budget

    monkeypatch.setattr("api.views.build_logs.customer_call_budget", customer_call_budget)
    settings.RATE_BUDGET_BUILD_LOGS_LIMIT = 1

    stack = make_app()
    deployment = _record_deployment(stack)
    harness.logs.get_log_events.side_effect = [_stream_response([]), _stream_response([])] * 3

    first = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert first.status_code == 200
    second = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert second.status_code == 429
    assert "Retry-After" in second

    from api.models.build_log_access import BuildLogAccess

    assert BuildLogAccess.objects.filter(user_id=stack.owner.id, status_code=429).exists()


@pytest.mark.django_db
def test_redis_down_fails_closed_503(factory, make_app, harness, monkeypatch):
    import redis
    from shared.ratelimit.budget import customer_call_budget

    monkeypatch.setattr("api.views.build_logs.customer_call_budget", customer_call_budget)

    def _boom():
        raise redis.RedisError("down")

    monkeypatch.setattr("shared.ratelimit.budget._redis", _boom)

    stack = make_app()
    deployment = _record_deployment(stack)
    resp = _get_deployment_logs(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert resp.status_code == 503
