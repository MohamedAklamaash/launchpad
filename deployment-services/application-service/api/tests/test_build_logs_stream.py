"""SSE build-logs streams (`GET .../deployments/<id>/build-logs/stream` and
`GET .../build-logs/current/stream`): framing, the mock-build shortcut, the in-progress
path's own re-check of `Application.status`/`build_id` on every tick, `Last-Event-ID`
resume, the owner-only authz ladder, the per-user rate budget, and the per-user
concurrent-stream cap shared with runtime_logs.py's stream.

Mirrors test_build_logs.py's fixtures (`harness`, `make_app`, `_record_deployment`)
rather than importing them, private to that module, same convention as this codebase's
other sibling test files."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import aws.platform_builder as pb_mod
import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.enums.user_role import UserRole

from api.views import build_logs as build_logs_view_mod
from api.views.build_logs import build_logs_stream, current_build_logs_stream

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
            "AccessKeyId": "SCOPED_AK", "SecretAccessKey": "SCOPED_SK", "SessionToken": "SCOPED_TOKEN",
            "Expiration": datetime.now(timezone.utc) + timedelta(minutes=15),
        }
    }


# ── fake redis (mirrors test_runtime_logs_stream.py's) ──────────────────────────

class _FakeRedis:
    def __init__(self):
        self.counts = {}
        self.expiry = {}

    def pipeline(self):
        return _FakePipeline(self)

    def expire(self, key, seconds):
        self.expiry[key] = seconds

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def decr(self, key):
        self.counts[key] = self.counts.get(key, 0) - 1
        return self.counts[key]

    def delete(self, key):
        self.counts.pop(key, None)
        self.expiry.pop(key, None)


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


@pytest.fixture(autouse=True)
def prod_mode(monkeypatch):
    prod = SimpleNamespace(mode="prod", aws_access_key_id="PLATFORM_AKID", aws_secret_access_key="PLATFORM_SECRET")
    monkeypatch.setattr(pb_mod, "app_config", prod)
    for key, value in ENV_VARS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _default_allow_budget(monkeypatch):
    monkeypatch.setattr("api.views.build_logs.customer_call_budget", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _default_allow_concurrency(monkeypatch):
    monkeypatch.setattr("api.views.build_logs.acquire_concurrency_slot", lambda *a, **k: True)
    monkeypatch.setattr("api.views.build_logs.release_concurrency_slot", lambda *a, **k: None)


@pytest.fixture
def harness(monkeypatch):
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
            assert service_name == "logs"
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


def _open_deployment(factory, user, app_id, deployment_id, last_event_id=None):
    request = factory.get(f"/api/v1/applications/{app_id}/deployments/{deployment_id}/build-logs/stream/")
    if last_event_id:
        request.META["HTTP_LAST_EVENT_ID"] = last_event_id
    force_authenticate(request, user=user)
    return build_logs_stream(request, app_id=app_id, deployment_id=deployment_id)


def _open_current(factory, user, app_id, last_event_id=None):
    request = factory.get(f"/api/v1/applications/{app_id}/build-logs/current/stream/")
    if last_event_id:
        request.META["HTTP_LAST_EVENT_ID"] = last_event_id
    force_authenticate(request, user=user)
    return current_build_logs_stream(request, app_id=app_id)


def _pull(response):
    return next(response.streaming_content)


def _parse_frame(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    out = {"event": None, "id": None, "data": None}
    for line in text.strip("\n").split("\n"):
        if line.startswith("event: "):
            out["event"] = line[len("event: "):]
        elif line.startswith("id: "):
            out["id"] = line[len("id: "):]
        elif line.startswith("data: "):
            out["data"] = json.loads(line[len("data: "):])
    return out


# ── authz ladder ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_stranger_gets_404(factory, make_app, make_user, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    resp = _open_deployment(factory, make_user(), str(stack.app.id), str(deployment.id))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_invited_admin_gets_403(factory, make_app, make_user, harness):
    from api.models.infrastructure_user_role import InfrastructureUserRole

    stack = make_app()
    deployment = _record_deployment(stack)
    invited = make_user()
    stack.infra.invited_users.add(invited)
    InfrastructureUserRole.objects.create(infrastructure=stack.infra, user=invited, role=UserRole.ADMIN)

    resp = _open_deployment(factory, invited, str(stack.app.id), str(deployment.id))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_legacy_build_gets_409(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack, build_id="codebuild-id-123", build_task_id=None)
    resp = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert resp.status_code == 409
    assert resp.data["code"] == "legacy_build"


@pytest.mark.django_db
def test_unknown_deployment_gets_404(factory, make_app, harness):
    stack = make_app()
    resp = _open_deployment(factory, stack.owner, str(stack.app.id), str(uuid.uuid4()))
    assert resp.status_code == 404


# ── framing / build-finished on the completed-deployment path ──────────────────

@pytest.mark.django_db
def test_owner_opens_stream_with_sse_headers(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    harness.logs.get_log_events.side_effect = [_stream_response([]), _stream_response([])]

    resp = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id))

    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/event-stream"
    assert resp["Cache-Control"] == "no-cache, no-store"
    assert resp["X-Accel-Buffering"] == "no"
    resp.close()


@pytest.mark.django_db
def test_events_then_build_finished_once_caught_up(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(stack)
    harness.logs.get_log_events.side_effect = [
        _stream_response([_log_event("fetching s3 config", 1000)]),
        _stream_response([_log_event("step build --dockerfile Dockerfile", 2000)]),
    ]

    resp = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id))
    first = _parse_frame(_pull(resp))
    second = _parse_frame(_pull(resp))

    assert first["event"] == "message"
    assert {e["container"] for e in first["data"]["events"]} == {"init", "kaniko"}
    assert second["event"] == "end"
    assert second["data"]["reason"] == "build_finished"
    resp.close()


# ── mock build ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_mock_build_emits_one_event_then_ends_without_any_aws_call(factory, make_app, harness):
    stack = make_app()
    deployment = _record_deployment(
        stack, build_id="mock-build-1", build_task_id=f"{pb_mod.MOCK_TASK_ID_PREFIX}mock-build-1",
    )

    resp = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id))
    first = _parse_frame(_pull(resp))
    second = _parse_frame(_pull(resp))

    assert first["event"] == "message"
    assert len(first["data"]["events"]) == 1
    assert second["event"] == "end"
    assert second["data"]["reason"] == "build_finished"
    harness.sts.assume_role.assert_not_called()
    harness.logs.get_log_events.assert_not_called()
    resp.close()


# ── in-progress path re-checks status every tick ────────────────────────────

@pytest.mark.django_db
def test_current_build_ends_once_application_leaves_building(factory, make_app, harness, settings, monkeypatch):
    settings.LOG_STREAM_TICK_SECONDS = 0
    settings.LOG_STREAM_HEARTBEAT_SECONDS = 0
    stack = make_app()
    stack.app.status = "BUILDING"
    stack.app.build_id = "build-1"
    stack.app.build_task_id = "task-1"
    stack.app.save(update_fields=["status", "build_id", "build_task_id"])

    monkeypatch.setattr(
        build_logs_view_mod.build_logs_service, "tail_stream", lambda ctx, tokens: ([], tokens, True),
    )

    resp = _open_current(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200

    first = _parse_frame(_pull(resp))
    assert first["event"] == "heartbeat"  # still BUILDING, no end frame yet

    from api.models.application import Application

    Application.objects.filter(pk=stack.app.pk).update(status="ACTIVE")

    second = _parse_frame(_pull(resp))
    assert second["event"] == "end"
    assert second["data"]["reason"] == "build_finished"
    resp.close()


@pytest.mark.django_db
def test_current_build_404_when_not_building(factory, make_app, harness):
    stack = make_app()
    stack.app.status = "ACTIVE"
    stack.app.build_id = "build-1"
    stack.app.build_task_id = "task-1"
    stack.app.save(update_fields=["status", "build_id", "build_task_id"])

    resp = _open_current(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 404


# ── resume via Last-Event-ID (reuses the JSON endpoint's own cursor) ───────────

@pytest.mark.django_db
def test_resume_passes_decoded_tokens_to_the_tail_loop(factory, make_app, harness, settings, monkeypatch):
    from api.services.build_logs_cursor import encode_cursor

    settings.LOG_STREAM_TICK_SECONDS = 0
    settings.LOG_STREAM_HEARTBEAT_SECONDS = 0
    stack = make_app()
    deployment = _record_deployment(stack, build_id="build-1")
    cursor = encode_cursor(
        app_id=str(stack.app.id), user_id=str(stack.owner.id), build_id="build-1",
        tokens={"init": "f/1", "kaniko": "f/2"},
    )

    seen = {}

    def _capture(ctx, tokens):
        seen["tokens"] = tokens
        return [], tokens, True

    monkeypatch.setattr(build_logs_view_mod.build_logs_service, "tail_stream", _capture)

    resp = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id), last_event_id=cursor)
    _pull(resp)

    assert seen["tokens"] == {"init": "f/1", "kaniko": "f/2"}
    resp.close()


@pytest.mark.django_db
def test_resume_with_tampered_cursor_falls_back_to_empty_tokens(factory, make_app, harness, settings, monkeypatch):
    settings.LOG_STREAM_TICK_SECONDS = 0
    settings.LOG_STREAM_HEARTBEAT_SECONDS = 0
    stack = make_app()
    deployment = _record_deployment(stack, build_id="build-1")

    seen = {}

    def _capture(ctx, tokens):
        seen["tokens"] = tokens
        return [], tokens, True

    monkeypatch.setattr(build_logs_view_mod.build_logs_service, "tail_stream", _capture)

    resp = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id), last_event_id="garbage")
    _pull(resp)

    assert seen["tokens"] == {}
    resp.close()


# ── rate budget ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_budget_exhausted_returns_429(factory, make_app, harness, fake_redis, settings, monkeypatch):
    from shared.ratelimit.budget import customer_call_budget

    monkeypatch.setattr("api.views.build_logs.customer_call_budget", customer_call_budget)
    settings.RATE_BUDGET_BUILD_LOGS_LIMIT = 1
    stack = make_app()
    deployment = _record_deployment(stack, build_id="mock-build-1", build_task_id=f"{pb_mod.MOCK_TASK_ID_PREFIX}mock-build-1")

    first = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id))
    second = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id))

    assert first.status_code == 200
    assert second.status_code == 429
    assert "Retry-After" in second


# ── concurrency cap + release on close ──────────────────────────────────────

@pytest.mark.django_db
def test_concurrency_cap_rejects_a_second_stream_until_the_first_releases(
    factory, make_app, harness, fake_redis, settings, monkeypatch,
):
    from shared.ratelimit.budget import (
        acquire_concurrency_slot,
        release_concurrency_slot,
    )

    monkeypatch.setattr("api.views.build_logs.acquire_concurrency_slot", acquire_concurrency_slot)
    monkeypatch.setattr("api.views.build_logs.release_concurrency_slot", release_concurrency_slot)
    settings.LOG_STREAM_MAX_CONCURRENT_PER_USER = 1
    stack = make_app()
    deployment = _record_deployment(stack, build_id="mock-build-1", build_task_id=f"{pb_mod.MOCK_TASK_ID_PREFIX}mock-build-1")

    first = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert first.status_code == 200
    _pull(first)  # starts the generator so .close() below actually runs its `finally`

    second = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert second.status_code == 429

    first.close()

    third = _open_deployment(factory, stack.owner, str(stack.app.id), str(deployment.id))
    assert third.status_code == 200
