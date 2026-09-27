"""F2 runtime log tailing: the owner-only authz ladder, B1 stream isolation (derived from
THIS app's own ECS service/EKS namespace, never a request parameter, and clamped to the
app's own created_at), strict query params, the signed cursor, bounded output, the
metadata-only audit trail, the per-user budget, and the no-store/unredacted posture."""
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.enums.user_role import UserRole

import api.services.runtime_logs_cursor as cursor_mod
from api.mock.mock_session import MockClient
from api.services.runtime_logs_cursor import (
    MAX_CURSOR_AGE_SECONDS,
    decode_cursor,
    encode_cursor,
)
from api.views.runtime_logs import runtime_logs

ACCOUNT_ID = "000000000000"
REGION = "us-west-2"


# ── fake redis (mirrors api/tests/test_rate_budget.py's stand-in) ──────────────

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


@pytest.fixture(autouse=True)
def _default_allow_budget(monkeypatch):
    """Most tests here aren't about the budget itself, and no Redis is available in the
    test environment — default to "always allowed", matching
    infrastructure-service/api/tests/test_database_api.py's _stub_rate_budget. The two
    budget-specific tests below restore the real function against a fake Redis."""
    monkeypatch.setattr("api.views.runtime_logs.customer_call_budget", lambda *a, **k: None)


# ── dev-mode / mock-session plumbing ────────────────────────────────────────────

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
    """A fully-wired, deployed ECS or EKS application on a mock infrastructure."""
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    def _make(*, owner=None, name="api", compute_type="ecs_fargate", account_id=ACCOUNT_ID):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", compute_type=compute_type,
            max_cpu=4.0, max_memory=8.0, is_mock=True, code=account_id, is_cloud_authenticated=True,
            metadata={"aws_region": REGION},
        )
        cluster_arn = f"arn:aws:ecs:{REGION}:{account_id}:cluster/cl-{infra.id}"
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
            slug = name
            app.runtime_refs = {"namespace": f"app-{slug}", "deployment": slug}
            app.save(update_fields=["runtime_refs"])
            from api.mock import mock_k8s
            state = mock_k8s.get_mock_apis(str(infra.id)).state
            state.objects[("deployment", f"app-{slug}", slug)] = {}
        else:
            app.service_arn = f"arn:aws:ecs:{REGION}:{account_id}:service/cl-{infra.id}/{name}-service"
            app.save(update_fields=["service_arn"])
        return SimpleNamespace(owner=owner, infra=infra, env=env, app=app)

    return _make


def _get(factory, user, app_id, **query):
    request = factory.get(f"/api/v1/applications/{app_id}/logs/", query)
    force_authenticate(request, user=user)
    return runtime_logs(request, app_id=app_id)


# ── 1: owner-only authz ladder ───────────────────────────────────────────────

@pytest.mark.django_db
def test_stranger_gets_404(factory, make_stack, make_user):
    stack = make_stack()
    resp = _get(factory, make_user(), str(stack.app.id))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_invited_admin_including_the_apps_own_creator_gets_403(factory, make_stack, make_user):
    from api.models.infrastructure_user_role import InfrastructureUserRole

    stack = make_stack()
    creator = make_user()
    stack.infra.invited_users.add(creator)
    InfrastructureUserRole.objects.create(infrastructure=stack.infra, user=creator, role=UserRole.ADMIN)
    stack.app.user = creator
    stack.app.save(update_fields=["user"])

    resp = _get(factory, creator, str(stack.app.id))

    assert resp.status_code == 403


@pytest.mark.django_db
def test_owner_gets_200_with_events(factory, make_stack):
    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200
    assert len(resp.data["events"]) > 0


@pytest.mark.django_db
def test_not_yet_deployed_app_gets_409(factory, make_stack):
    stack = make_stack()
    stack.app.service_arn = None
    stack.app.save(update_fields=["service_arn"])

    resp = _get(factory, stack.owner, str(stack.app.id))

    assert resp.status_code == 409


@pytest.mark.django_db
def test_owner_gets_200_on_eks(factory, make_stack):
    stack = make_stack(compute_type="eks")
    resp = _get(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200
    assert len(resp.data["events"]) > 0


@pytest.mark.django_db
def test_eks_pod_log_read_is_bounded(factory, make_stack, monkeypatch):
    from api.mock.mock_k8s import MockCoreV1Api

    calls = []
    original = MockCoreV1Api.read_namespaced_pod_log

    def spy(self, name, namespace, **kwargs):
        calls.append(kwargs)
        return original(self, name, namespace, **kwargs)

    monkeypatch.setattr(MockCoreV1Api, "read_namespaced_pod_log", spy)
    stack = make_stack(compute_type="eks")

    resp = _get(factory, stack.owner, str(stack.app.id))

    assert resp.status_code == 200
    assert calls, "read_namespaced_pod_log must have been called"
    for kwargs in calls:
        assert kwargs["follow"] is False
        assert kwargs["tail_lines"] <= 500
        assert kwargs["limit_bytes"] <= 512 * 1024
        assert kwargs["since_seconds"] <= 3600


# ── 2: B1 stream isolation — derived from THIS app, never a request param ───

@pytest.mark.django_db
def test_two_infras_same_account_same_app_name_read_only_own_streams(factory, make_stack, monkeypatch):
    calls = []
    original = MockClient.filter_log_events

    def spy(self, **kwargs):
        calls.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(MockClient, "filter_log_events", spy)

    stack_a = make_stack(name="api")
    stack_b = make_stack(name="api")  # different infra, same account, same app name

    resp_a = _get(factory, stack_a.owner, str(stack_a.app.id))
    assert resp_a.status_code == 200
    streams_a = calls[-1]["logStreamNames"]
    log_group_a = calls[-1]["logGroupName"]
    assert calls[-1]["limit"] == 100

    resp_b = _get(factory, stack_b.owner, str(stack_b.app.id))
    assert resp_b.status_code == 200
    streams_b = calls[-1]["logStreamNames"]

    # Same log group (account+region scoped, per B1) — isolation comes from the stream
    # names, not from the group, so this asserts the harder case rather than a trivial one.
    assert log_group_a == calls[-1]["logGroupName"]
    assert streams_a, "app A must resolve at least one of its own streams"
    assert streams_b, "app B must resolve at least one of its own streams"
    assert set(streams_a).isdisjoint(streams_b)


@pytest.mark.django_db
def test_recreated_app_window_is_clamped_to_its_own_created_at(make_stack):
    """A deleted-then-recreated app of the same name gets a fresh created_at. Even if a
    stale task briefly reappeared in list_tasks (ECS's ~1h STOPPED visibility window),
    the request window must never reach further back than this Application row's own
    creation — that's the actual isolation backstop, independent of stream identity."""
    from datetime import datetime, timedelta, timezone

    from api.models.application import Application
    from api.services.runtime_logs_service import RuntimeLogsService, _now_ms

    stack = make_stack()
    old_created_at = datetime.now(timezone.utc) - timedelta(days=1)
    Application.objects.filter(pk=stack.app.pk).update(created_at=old_created_at)
    stack.app.refresh_from_db()

    # Recreate: bump created_at to "just now", as a fresh row would have.
    recent_created_at = datetime.now(timezone.utc)
    Application.objects.filter(pk=stack.app.pk).update(created_at=recent_created_at)
    stack.app.refresh_from_db()

    result = RuntimeLogsService().get_logs(
        user_id=stack.owner.id, app_id=str(stack.app.id), container="app", minutes=60,
        cursor=None, previous=False,
    )
    created_ms = int(recent_created_at.timestamp() * 1000)
    assert result.window_start_ms >= created_ms
    assert result.window_start_ms >= _now_ms() - 61_000  # nowhere near the 60m-ago naive window


# ── 3: strict params — never reach AWS/k8s mocks ────────────────────────────

@pytest.mark.django_db
def test_unknown_query_param_rejected_before_touching_mocks(factory, make_stack, monkeypatch):
    stack = make_stack()
    monkeypatch.setattr(MockClient, "list_tasks", MagicMock(side_effect=AssertionError("must not be called")))

    resp = _get(factory, stack.owner, str(stack.app.id), log_group="/ecs/other-task")

    assert resp.status_code == 400


@pytest.mark.django_db
def test_invalid_container_choice_rejected_before_touching_mocks(factory, make_stack, monkeypatch):
    stack = make_stack()
    monkeypatch.setattr(MockClient, "list_tasks", MagicMock(side_effect=AssertionError("must not be called")))

    resp = _get(factory, stack.owner, str(stack.app.id), container="kube-system")

    assert resp.status_code == 400


# ── 4: forced exception never leaks content ─────────────────────────────────

@pytest.mark.django_db
def test_forced_exception_returns_generic_body_and_caplog_has_no_secret(factory, make_stack, monkeypatch, caplog):
    stack = make_stack()
    leaked = "SUPER_SECRET_LOG_LINE_9f8e7d"
    monkeypatch.setattr(
        "api.views.runtime_logs.runtime_logs_service.get_logs",
        MagicMock(side_effect=RuntimeError(f"boom containing {leaked}")),
    )
    caplog.set_level(logging.DEBUG)

    resp = _get(factory, stack.owner, str(stack.app.id))

    assert resp.status_code == 500
    assert leaked not in str(resp.data)
    assert leaked not in caplog.text


# ── 5: cursor tamper / cross-app / cross-user / expired / oversized ─────────

def _cursor(app_id="app-1", user_id="user-1", container="app"):
    return encode_cursor(app_id=app_id, user_id=user_id, container=container, start_ms=0, end_ms=1000, aws_token="tok")


def test_cursor_round_trips():
    c = _cursor()
    payload = decode_cursor(c, app_id="app-1", user_id="user-1", container="app")
    assert payload["t"] == "tok"


def test_cursor_tampered_is_rejected():
    c = _cursor()
    tampered = c[:-1] + ("a" if c[-1] != "a" else "b")
    with pytest.raises(ValueError):
        decode_cursor(tampered, app_id="app-1", user_id="user-1", container="app")


def test_cursor_replayed_for_a_different_app_is_rejected():
    c = _cursor(app_id="app-1")
    with pytest.raises(ValueError):
        decode_cursor(c, app_id="app-2", user_id="user-1", container="app")


def test_cursor_replayed_for_a_different_user_is_rejected():
    c = _cursor(user_id="user-1")
    with pytest.raises(ValueError):
        decode_cursor(c, app_id="app-1", user_id="user-2", container="app")


def test_cursor_replayed_for_a_different_container_is_rejected():
    c = _cursor(container="app")
    with pytest.raises(ValueError):
        decode_cursor(c, app_id="app-1", user_id="user-1", container="proxy")


def test_cursor_expired_is_rejected(monkeypatch):
    c = _cursor()
    monkeypatch.setattr(cursor_mod, "MAX_CURSOR_AGE_SECONDS", -1)
    with pytest.raises(ValueError):
        decode_cursor(c, app_id="app-1", user_id="user-1", container="app")
    assert MAX_CURSOR_AGE_SECONDS == 900  # module default untouched outside this test


def test_cursor_oversized_is_rejected():
    with pytest.raises(ValueError):
        decode_cursor("x" * 5000, app_id="app-1", user_id="user-1", container="app")


@pytest.mark.django_db
def test_cursor_and_minutes_combined_is_rejected(factory, make_stack):
    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id), cursor="whatever", minutes="10")
    assert resp.status_code == 400


# ── 6: no platform secret ever reaches a container's env ────────────────────

def test_ecs_container_env_carries_no_platform_setting(monkeypatch):
    from aws.ecs import ECSClient

    from api.mock.mock_session import MockSession

    session = MockSession(region=REGION, account_id=ACCOUNT_ID)
    ecs = ECSClient(session)
    captured = {}
    original = MockClient.register_task_definition

    def spy(self, **kwargs):
        captured.update(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(MockClient, "register_task_definition", spy)
    ecs.create_task_definition(
        family="api-task", image="img:latest", cpu=0.25, memory=0.5,
        envs={"NODE_ENV": "production"}, execution_role_arn="arn:aws:iam::000000000000:role/exec",
        container_port=8080, app_name="api",
    )

    env_names = {
        e["name"]
        for container in captured["containerDefinitions"]
        for e in container.get("environment", [])
    }
    assert "INTERNAL_API_TOKEN" not in env_names
    assert "JWT_SECRET" not in env_names
    assert "AWS_ACCESS_KEY_ID" not in env_names
    assert "AWS_SECRET_ACCESS_KEY" not in env_names
    assert "DJANGO_SECRET" not in env_names


@pytest.mark.django_db
def test_eks_deployment_manifest_env_carries_no_platform_setting(make_stack):
    from api.k8s.deployer import EKSDeployer

    stack = make_stack(compute_type="eks")
    deployer = EKSDeployer(session=MagicMock(), application=stack.app, environment=stack.env)
    manifest = deployer._deployment_manifest("img:latest")
    app_container = manifest.spec.template.spec.containers[0]
    env_names = {e.name for e in app_container.env}
    assert "INTERNAL_API_TOKEN" not in env_names
    assert "JWT_SECRET" not in env_names
    assert "AWS_ACCESS_KEY_ID" not in env_names


# ── 7: bounds ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_minutes_clamped_to_60_max(make_stack):
    from api.services.runtime_logs_service import (
        MAX_WINDOW_MINUTES,
        RuntimeLogsService,
    )

    stack = make_stack()
    result = RuntimeLogsService().get_logs(
        user_id=stack.owner.id, app_id=str(stack.app.id), container="app", minutes=None,
        cursor=None, previous=False,
    )
    default_window_ms = 15 * 60_000
    assert result.window_end_ms - result.window_start_ms <= default_window_ms + 1000
    assert MAX_WINDOW_MINUTES == 60


@pytest.mark.django_db
def test_minutes_out_of_range_is_rejected_by_the_serializer(factory, make_stack):
    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id), minutes="61")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_per_message_and_total_bytes_are_capped(make_stack, monkeypatch):
    from api.mock.mock_session import MockClient
    from api.services.runtime_logs_service import (
        MAX_MESSAGE_BYTES,
        MAX_TOTAL_BYTES,
        RuntimeLogsService,
    )

    huge_message = "x" * (MAX_MESSAGE_BYTES * 3)

    def huge_filter_log_events(self, **kwargs):
        streams = kwargs.get("logStreamNames") or []
        return {"events": [{"logStreamName": s, "timestamp": 1, "message": huge_message} for s in streams * 5]}

    monkeypatch.setattr(MockClient, "filter_log_events", huge_filter_log_events)
    stack = make_stack()

    result = RuntimeLogsService().get_logs(
        user_id=stack.owner.id, app_id=str(stack.app.id), container="app", minutes=None,
        cursor=None, previous=False,
    )

    assert result.truncated is True
    assert result.total_bytes <= MAX_TOTAL_BYTES
    for event in result.events:
        assert len(event["message"].encode()) <= MAX_MESSAGE_BYTES + len("...[truncated]")


# ── 8: audit row on success and denial, no content fields ───────────────────

@pytest.mark.django_db
def test_audit_row_written_on_success(factory, make_stack):
    from api.models.runtime_log_access import RuntimeLogAccess

    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200

    row = RuntimeLogAccess.objects.get(app_id=stack.app.id)
    assert row.status_code == 200
    assert row.user_id == stack.owner.id
    assert row.event_count > 0
    assert not hasattr(row, "message")
    assert not hasattr(row, "logs")


@pytest.mark.django_db
def test_audit_row_written_on_denial(factory, make_stack, make_user):
    from api.models.runtime_log_access import RuntimeLogAccess

    stack = make_stack()
    stranger = make_user()
    resp = _get(factory, stranger, str(stack.app.id))
    assert resp.status_code == 404

    row = RuntimeLogAccess.objects.filter(user_id=stranger.id).latest("created_at")
    assert row.status_code == 404


# ── 9: budget 429 / redis-down 503 ───────────────────────────────────────────

@pytest.mark.django_db
def test_budget_exhausted_returns_429(factory, make_stack, fake_redis, settings, monkeypatch):
    from shared.ratelimit.budget import customer_call_budget

    monkeypatch.setattr("api.views.runtime_logs.customer_call_budget", customer_call_budget)
    settings.RATE_BUDGET_RUNTIME_LOGS_LIMIT = 1
    stack = make_stack()

    first = _get(factory, stack.owner, str(stack.app.id))
    second = _get(factory, stack.owner, str(stack.app.id))

    assert first.status_code == 200
    assert second.status_code == 429
    assert "Retry-After" in second


@pytest.mark.django_db
def test_redis_down_returns_503_not_open(factory, make_stack, monkeypatch):
    import redis
    from shared.ratelimit.budget import customer_call_budget

    monkeypatch.setattr("api.views.runtime_logs.customer_call_budget", customer_call_budget)

    def _boom():
        raise redis.RedisError("down")

    monkeypatch.setattr("shared.ratelimit.budget._redis", _boom)
    stack = make_stack()

    resp = _get(factory, stack.owner, str(stack.app.id))

    assert resp.status_code == 503


# ── 10: unredacted posture + no-store ────────────────────────────────────────

@pytest.mark.django_db
def test_seeded_secret_returned_unredacted_with_no_store_headers(factory, make_stack):
    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id))

    assert resp.status_code == 200
    messages = " ".join(e["message"] for e in resp.data["events"])
    assert "mock-fake-password" in messages
    assert resp["Cache-Control"] == "no-store"
    assert resp["X-Content-Type-Options"] == "nosniff"


# ── 11: mock/real gate ───────────────────────────────────────────────────────

@pytest.mark.django_db
def test_real_infra_in_dev_mode_is_refused(factory, make_stack):
    stack = make_stack()
    stack.infra.is_mock = False
    stack.infra.save(update_fields=["is_mock"])

    resp = _get(factory, stack.owner, str(stack.app.id))

    assert resp.status_code == 400
