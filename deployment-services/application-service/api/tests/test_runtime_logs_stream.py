"""SSE runtime-logs stream (`GET .../logs/stream`): framing, heartbeat, max duration,
`Last-Event-ID` resume, the owner-only authz ladder, the per-user rate budget, the
per-user concurrent-stream cap, and slot release on both a normal end and a client
disconnect (`StreamingHttpResponse.close()`).

Mirrors test_runtime_logs.py's fixtures (`factory`, `make_user`, `make_stack`, the
dev-mode/mock-session plumbing) rather than importing them, those fixtures are private
to that module, same as this codebase's other sibling test files."""
import json
import uuid
from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.enums.user_role import UserRole

from api.views import runtime_logs as runtime_logs_view_mod
from api.views.runtime_logs import runtime_logs_stream

ACCOUNT_ID = "000000000000"
REGION = "us-west-2"


# ── fake redis (mirrors test_runtime_logs.py's) ─────────────────────────────────

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
def _default_allow_budget(monkeypatch):
    monkeypatch.setattr("api.views.runtime_logs.customer_call_budget", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _default_allow_concurrency(monkeypatch):
    monkeypatch.setattr("api.views.runtime_logs.acquire_concurrency_slot", lambda *a, **k: True)
    monkeypatch.setattr("api.views.runtime_logs.release_concurrency_slot", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def dev_mode(monkeypatch):
    import aws.session as aws_session_mod

    from api.k8s import deployer as deployer_mod

    dev = SimpleNamespace(mode="dev")
    monkeypatch.setattr(aws_session_mod, "app_config", dev)
    monkeypatch.setattr(deployer_mod, "app_config", dev)


@pytest.fixture(autouse=True)
def clean_mock_k8s():
    from api.mock import mock_k8s
    mock_k8s.reset()
    yield
    mock_k8s.reset()


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def make_user(schema_db):
    from api.models.user import User

    def _make():
        return User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")

    return _make


@pytest.fixture
def make_stack(make_user):
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    def _make(*, owner=None, name="api", compute_type="ecs_fargate"):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", compute_type=compute_type,
            max_cpu=4.0, max_memory=8.0, is_mock=True, code=ACCOUNT_ID, is_cloud_authenticated=True,
            metadata={"aws_region": REGION},
        )
        cluster_arn = f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:cluster/cl-{infra.id}"
        env = Environment.objects.create(
            infrastructure=infra, status="ACTIVE", vpc_id="vpc-1", cluster_arn=cluster_arn,
            alb_dns="alb.example.com",
        )
        app = Application.objects.create(
            user=owner, infrastructure=infra, name=name, project_remote_url="https://github.com/o/r",
            project_branch="main", project_commit_hash="a" * 40, port=8080,
            alloted_cpu=0.5, alloted_memory=1.0,
        )
        if compute_type == "eks":
            app.runtime_refs = {"namespace": f"app-{name}", "deployment": name}
            app.save(update_fields=["runtime_refs"])
            from api.mock import mock_k8s
            state = mock_k8s.get_mock_apis(str(infra.id)).state
            state.objects[("deployment", f"app-{name}", name)] = {}
        else:
            app.service_arn = f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:service/cl-{infra.id}/{name}-service"
            app.save(update_fields=["service_arn"])
        return SimpleNamespace(owner=owner, infra=infra, env=env, app=app)

    return _make


def _open(factory, user, app_id, last_event_id=None, **query):
    request = factory.get(f"/api/v1/applications/{app_id}/logs/stream/", query)
    if last_event_id:
        request.META["HTTP_LAST_EVENT_ID"] = last_event_id
    force_authenticate(request, user=user)
    return runtime_logs_stream(request, app_id=app_id)


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
def test_stranger_gets_404(factory, make_stack, make_user):
    stack = make_stack()
    resp = _open(factory, make_user(), str(stack.app.id))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_invited_admin_gets_403(factory, make_stack, make_user):
    from api.models.infrastructure_user_role import InfrastructureUserRole

    stack = make_stack()
    invited = make_user()
    stack.infra.invited_users.add(invited)
    InfrastructureUserRole.objects.create(infrastructure=stack.infra, user=invited, role=UserRole.ADMIN)

    resp = _open(factory, invited, str(stack.app.id))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_not_yet_deployed_gets_409(factory, make_stack):
    stack = make_stack()
    stack.app.service_arn = None
    stack.app.save(update_fields=["service_arn"])

    resp = _open(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 409


@pytest.mark.django_db
def test_unknown_query_param_gets_400(factory, make_stack):
    stack = make_stack()
    resp = _open(factory, stack.owner, str(stack.app.id), bogus="1")
    assert resp.status_code == 400


# ── framing ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_owner_opens_stream_with_sse_headers(factory, make_stack):
    stack = make_stack()
    resp = _open(factory, stack.owner, str(stack.app.id))

    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/event-stream"
    assert resp["Cache-Control"] == "no-cache, no-store"
    assert resp["X-Accel-Buffering"] == "no"
    assert "X-Request-Id" in resp
    resp.close()


@pytest.mark.django_db
def test_event_frame_is_well_formed(factory, make_stack, settings, monkeypatch):
    settings.LOG_STREAM_TICK_SECONDS = 0
    stack = make_stack()
    monkeypatch.setattr(
        runtime_logs_view_mod.runtime_logs_service, "tail_stream",
        lambda ctx, wm: ([{"timestamp": "2024-01-01T00:00:00Z", "message": "hello"}], {"ts": 1, "ids": ["a"]}),
    )

    resp = _open(factory, stack.owner, str(stack.app.id))
    frame = _parse_frame(_pull(resp))

    assert frame["event"] == "message"
    assert frame["id"] is not None
    assert frame["data"] == {"events": [{"timestamp": "2024-01-01T00:00:00Z", "message": "hello"}]}
    resp.close()


# ── heartbeat ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_heartbeat_sent_on_a_quiet_tick(factory, make_stack, settings, monkeypatch):
    settings.LOG_STREAM_TICK_SECONDS = 0
    settings.LOG_STREAM_HEARTBEAT_SECONDS = 0
    stack = make_stack()
    monkeypatch.setattr(
        runtime_logs_view_mod.runtime_logs_service, "tail_stream",
        lambda ctx, wm: ([], wm or {"ts": 123, "ids": []}),
    )

    resp = _open(factory, stack.owner, str(stack.app.id))
    frame = _parse_frame(_pull(resp))

    # Carries the current watermark's id rather than a bare `: ping` comment, so a
    # reconnect's Last-Event-ID stays fresh even on a stream with nothing to tail.
    assert frame["event"] == "heartbeat"
    assert frame["id"] is not None
    resp.close()


@pytest.mark.django_db
def test_first_tick_emits_a_frame_without_waiting_a_heartbeat_interval(
    factory, make_stack, settings, monkeypatch,
):
    """No bytes (headers included) reach the client before the first frame. Real run:
    a quiet stream sent nothing for ~15 s, so every (re)connect looked hung."""
    settings.LOG_STREAM_TICK_SECONDS = 0
    settings.LOG_STREAM_HEARTBEAT_SECONDS = 3600
    stack = make_stack()
    monkeypatch.setattr(
        runtime_logs_view_mod.runtime_logs_service, "tail_stream",
        lambda ctx, wm: ([], wm or {"ts": 123, "ids": []}),
    )

    resp = _open(factory, stack.owner, str(stack.app.id))
    frame = _parse_frame(_pull(resp))

    assert frame["event"] == "heartbeat"
    resp.close()


@pytest.mark.django_db
def test_heartbeat_id_advances_across_quiet_ticks(factory, make_stack, settings, monkeypatch):
    """A resumed stream's lookback window (ECS_WATERMARK_LOOKBACK_MS) is anchored to
    whatever `ts` the last frame's id carried. Without this, a long quiet period would
    leave that id stuck on an old `message` frame and a reconnect would re-fetch the
    whole gap."""
    settings.LOG_STREAM_TICK_SECONDS = 0
    settings.LOG_STREAM_HEARTBEAT_SECONDS = 0
    stack = make_stack()
    ticks = iter([100, 200, 300])
    monkeypatch.setattr(
        runtime_logs_view_mod.runtime_logs_service, "tail_stream",
        lambda ctx, wm: ([], {"ts": next(ticks), "ids": []}),
    )

    resp = _open(factory, stack.owner, str(stack.app.id))
    first_id = _parse_frame(_pull(resp))["id"]
    second_id = _parse_frame(_pull(resp))["id"]

    assert first_id != second_id
    resp.close()


# ── max duration ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_max_duration_ends_the_stream_and_releases_the_slot(factory, make_stack, settings, monkeypatch):
    settings.LOG_STREAM_MAX_DURATION_SECONDS = -1
    released = []
    monkeypatch.setattr("api.views.runtime_logs.release_concurrency_slot", lambda key: released.append(key))
    stack = make_stack()

    resp = _open(factory, stack.owner, str(stack.app.id))
    frame = _parse_frame(_pull(resp))
    assert frame["event"] == "end"
    assert frame["data"]["reason"] == "max_duration"

    with pytest.raises(StopIteration):
        next(resp.streaming_content)
    assert released, "the concurrency slot must be released once the generator finishes"


# ── upstream error ───────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_upstream_error_emits_error_then_end(factory, make_stack, settings, monkeypatch):
    from api.services.runtime_logs_service import UpstreamError

    settings.LOG_STREAM_TICK_SECONDS = 0
    stack = make_stack()

    def _raise(ctx, wm):
        raise UpstreamError("boom", code="ThrottlingException")

    monkeypatch.setattr(runtime_logs_view_mod.runtime_logs_service, "tail_stream", _raise)

    resp = _open(factory, stack.owner, str(stack.app.id))
    first = _parse_frame(_pull(resp))
    second = _parse_frame(_pull(resp))

    assert first["event"] == "error"
    assert first["data"]["code"] == "ThrottlingException"
    assert second["event"] == "end"
    assert second["data"]["reason"] == "upstream_error"
    resp.close()


# ── resume via Last-Event-ID ─────────────────────────────────────────────────

@pytest.mark.django_db
def test_resume_decodes_watermark_from_last_event_id(factory, make_stack, settings, monkeypatch):
    from api.services.runtime_logs_stream_cursor import encode_watermark

    settings.LOG_STREAM_TICK_SECONDS = 0
    settings.LOG_STREAM_HEARTBEAT_SECONDS = 0
    stack = make_stack()
    cursor = encode_watermark(app_id=stack.app.id, user_id=stack.owner.id, container="app", ts_ms=12345, ids=["x"])

    seen = {}

    def _capture(ctx, wm):
        seen["watermark"] = wm
        return [], wm

    monkeypatch.setattr(runtime_logs_view_mod.runtime_logs_service, "tail_stream", _capture)

    resp = _open(factory, stack.owner, str(stack.app.id), last_event_id=cursor)
    _pull(resp)  # heartbeat or quiet tick, just need the generator to have run once

    assert seen["watermark"]["ts"] == 12345
    assert seen["watermark"]["ids"] == ["x"]
    resp.close()


@pytest.mark.django_db
def test_resume_with_a_foreign_cursor_falls_back_to_a_fresh_tail(factory, make_stack, make_user, settings, monkeypatch):
    """A cursor minted for a different app/user/container must not be honored, rather
    than 400ing the reconnect, it's treated the same as a missing one."""
    from api.services.runtime_logs_stream_cursor import encode_watermark

    settings.LOG_STREAM_TICK_SECONDS = 0
    settings.LOG_STREAM_HEARTBEAT_SECONDS = 0
    stack = make_stack()
    other = make_user()
    foreign_cursor = encode_watermark(app_id=stack.app.id, user_id=other.id, container="app", ts_ms=1, ids=[])

    seen = {}

    def _capture(ctx, wm):
        seen["watermark"] = wm
        return [], wm or {"ts": 999, "ids": []}

    monkeypatch.setattr(runtime_logs_view_mod.runtime_logs_service, "tail_stream", _capture)

    resp = _open(factory, stack.owner, str(stack.app.id), last_event_id=foreign_cursor)
    _pull(resp)

    assert seen["watermark"] is None
    resp.close()


# ── rate budget ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_budget_exhausted_returns_429(factory, make_stack, fake_redis, settings, monkeypatch):
    from shared.ratelimit.budget import customer_call_budget

    monkeypatch.setattr("api.views.runtime_logs.customer_call_budget", customer_call_budget)
    settings.RATE_BUDGET_RUNTIME_LOGS_LIMIT = 1
    stack = make_stack()

    first = _open(factory, stack.owner, str(stack.app.id))
    first.close()
    second = _open(factory, stack.owner, str(stack.app.id))

    assert first.status_code == 200
    assert second.status_code == 429
    assert "Retry-After" in second


# ── concurrency cap + release on close ──────────────────────────────────────

@pytest.mark.django_db
def test_concurrency_cap_rejects_a_second_stream_until_the_first_releases(
    factory, make_stack, fake_redis, settings, monkeypatch,
):
    from shared.ratelimit.budget import (
        acquire_concurrency_slot,
        release_concurrency_slot,
    )

    monkeypatch.setattr("api.views.runtime_logs.acquire_concurrency_slot", acquire_concurrency_slot)
    monkeypatch.setattr("api.views.runtime_logs.release_concurrency_slot", release_concurrency_slot)
    settings.LOG_STREAM_MAX_CONCURRENT_PER_USER = 1
    stack = make_stack()

    first = _open(factory, stack.owner, str(stack.app.id))
    assert first.status_code == 200
    # A never-iterated generator's .close() is a no-op (Python only runs `finally` once
    # the generator has actually started), pull one frame first, exactly like the WSGI
    # handler that starts consuming `streaming_content` as soon as it has a response to
    # send, so the slot's eventual release below is real.
    _pull(first)

    second = _open(factory, stack.owner, str(stack.app.id))
    assert second.status_code == 429

    first.close()  # drives the generator's finally, releasing the slot

    third = _open(factory, stack.owner, str(stack.app.id))
    assert third.status_code == 200
    third.close()


@pytest.mark.django_db
def test_disconnect_releases_the_slot(factory, make_stack, fake_redis, settings, monkeypatch):
    """`response.close()` is what Django's StreamingHttpResponse calls on the generator
    when the client disconnects (or the test is done with it), this is the same code
    path, exercised directly rather than through a real socket."""
    from shared.ratelimit.budget import (
        acquire_concurrency_slot,
        release_concurrency_slot,
    )

    monkeypatch.setattr("api.views.runtime_logs.acquire_concurrency_slot", acquire_concurrency_slot)
    monkeypatch.setattr("api.views.runtime_logs.release_concurrency_slot", release_concurrency_slot)
    settings.LOG_STREAM_MAX_CONCURRENT_PER_USER = 1
    stack = make_stack()

    resp = _open(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200
    _pull(resp)  # advance the generator to a suspended `yield` so `.close()` can inject GeneratorExit

    resp.close()

    second = _open(factory, stack.owner, str(stack.app.id))
    assert second.status_code == 200
    second.close()


# ── EKS: the held-open k8s_apis context manager ─────────────────────────────

@pytest.mark.django_db
def test_eks_stream_opens_pulls_a_frame_and_closes_cleanly(factory, make_stack):
    stack = make_stack(compute_type="eks")

    resp = _open(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200

    _pull(resp)  # one tick through the real (mock) k8s_apis context manager
    resp.close()  # must exit that context manager without raising


@pytest.mark.django_db
def test_eks_stream_close_exits_the_k8s_context_manager(factory, make_stack, monkeypatch):
    """`StreamContext.close()` is patched at the class level, so a *different* test's
    stream whose generator was never exhausted (left suspended at a `yield`, only ever
    reclaimed later by garbage collection, which calls `.close()` on it the same way a
    dropped client connection would) can fire this spy too, at an unpredictable moment
    during this test's own run. Filtering to the one `StreamContext` this test itself
    opened is what makes the assertion mean anything regardless of that GC noise."""
    from api.services import runtime_logs_service as svc_mod

    stack = make_stack(compute_type="eks")
    own_ctx = {}
    events = []
    original_open_stream = svc_mod.RuntimeLogsService.open_stream
    original_close = svc_mod.StreamContext.close

    def spy_open_stream(self, **kwargs):
        ctx = original_open_stream(self, **kwargs)
        own_ctx["ctx"] = ctx
        return ctx

    def spy_close(self):
        if self is own_ctx.get("ctx"):
            events.append(("before", self.k8s_cm is not None))
        original_close(self)
        if self is own_ctx.get("ctx"):
            events.append(("after", self.k8s_cm is None))

    monkeypatch.setattr(svc_mod.RuntimeLogsService, "open_stream", spy_open_stream)
    monkeypatch.setattr(svc_mod.StreamContext, "close", spy_close)

    resp = _open(factory, stack.owner, str(stack.app.id))
    _pull(resp)
    resp.close()

    assert events == [("before", True), ("after", True)]


# ── out-of-order events across ECS tasks (lookback + eventId dedup) ─────────

@pytest.mark.django_db
def test_late_arriving_event_from_another_task_is_not_lost(factory, make_stack, monkeypatch):
    """CloudWatch can land one task's event after another task's later-timestamped one
    already has. A watermark that only ever moved forward by timestamp would drop that
    late arrival for good the moment it advanced past it; the lookback window plus
    `ctx.ecs_seen` recovers it on the very next tick instead."""
    from api.mock.mock_session import MockClient

    stack = make_stack()
    ctx = runtime_logs_view_mod.runtime_logs_service.open_stream(
        user_id=stack.owner.id, app_id=str(stack.app.id), container="app",
    )

    calls = {"n": 0}

    def fake_filter(self, **kwargs):
        calls["n"] += 1
        end_time = kwargs["endTime"]
        if calls["n"] == 1:
            return {"events": [{"eventId": "e-second", "timestamp": end_time, "message": "second"}]}
        return {"events": [
            {"eventId": "e-second", "timestamp": end_time - 100, "message": "second"},
            {"eventId": "e-first", "timestamp": end_time - 5000, "message": "first, arrived late"},
        ]}

    monkeypatch.setattr(MockClient, "filter_log_events", fake_filter)

    events1, watermark1 = runtime_logs_view_mod.runtime_logs_service.tail_stream(ctx, None)
    assert [e["message"] for e in events1] == ["second"]

    events2, _ = runtime_logs_view_mod.runtime_logs_service.tail_stream(ctx, watermark1)
    assert [e["message"] for e in events2] == ["first, arrived late"]
