"""Per-app metrics: the owner-only authz ladder (shared with runtime logs via
api/services/owner_only.py), query construction per compute type, stat/period mapping,
ms conversion for ALB latency, empty/unavailable series, AccessDenied -> 422,
throttling -> 503, not-deployed -> 409, range validation, and the 30s response cache."""
import uuid
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.enums.user_role import UserRole

from api.services import app_metrics_service as metrics_mod
from api.services.app_metrics_service import AppMetricsService
from api.views.app_metrics import application_metrics

ACCOUNT_ID = "000000000000"
REGION = "us-west-2"


# ── fake redis (mirrors api/tests/test_rate_budget.py's stand-in, and
# test_runtime_logs.py's _FakeRedis for the rate-limit budget — this one backs the
# metrics response cache instead) ───────────────────────────────────────────────

class _FakeCacheRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, _ttl, value):
        self.store[key] = value


@pytest.fixture(autouse=True)
def fake_cache(monkeypatch):
    fake = _FakeCacheRedis()
    monkeypatch.setattr(metrics_mod.redis, "Redis", lambda connection_pool=None: fake)
    return fake


@pytest.fixture(autouse=True)
def _default_allow_budget(monkeypatch):
    monkeypatch.setattr("api.views.app_metrics.customer_call_budget", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def dev_mode(monkeypatch):
    """Default every test to the mock/dev path (matches make_stack's is_mock=True) —
    real-path tests explicitly restore prod mode on metrics_mod.app_config."""
    import aws.session as aws_session_mod

    dev = SimpleNamespace(mode="dev")
    monkeypatch.setattr(aws_session_mod, "app_config", dev)
    monkeypatch.setattr(metrics_mod, "app_config", dev)


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

    def _make(*, owner=None, name="api", compute_type="ecs_fargate", account_id=ACCOUNT_ID, is_mock=True):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", compute_type=compute_type,
            max_cpu=4.0, max_memory=8.0, is_mock=is_mock, code=account_id, is_cloud_authenticated=True,
            metadata={"aws_region": REGION},
        )
        cluster_arn = f"arn:aws:ecs:{REGION}:{account_id}:cluster/cl-{infra.id}"
        env = Environment.objects.create(
            infrastructure=infra, status="ACTIVE", vpc_id="vpc-1", cluster_arn=cluster_arn,
            alb_dns="alb.example.com",
            alb_arn=f"arn:aws:elasticloadbalancing:{REGION}:{account_id}:loadbalancer/app/lb/abc123",
        )
        app = Application.objects.create(
            user=owner, infrastructure=infra, name=name, project_remote_url="https://github.com/o/r",
            project_branch="main", project_commit_hash="a" * 40, port=8080,
            alloted_cpu=0.5, alloted_memory=1.0,
            target_group_arn=f"arn:aws:elasticloadbalancing:{REGION}:{account_id}:targetgroup/tg/def456",
        )
        if compute_type == "eks":
            slug = name
            app.runtime_refs = {"namespace": f"app-{slug}", "deployment": slug}
            app.save(update_fields=["runtime_refs"])
        else:
            app.service_arn = f"arn:aws:ecs:{REGION}:{account_id}:service/cl-{infra.id}/{name}-service"
            app.save(update_fields=["service_arn"])
        return SimpleNamespace(owner=owner, infra=infra, env=env, app=app)

    return _make


def _get(factory, user, app_id, **query):
    request = factory.get(f"/api/v1/applications/{app_id}/metrics/", query)
    force_authenticate(request, user=user)
    return application_metrics(request, app_id=app_id)


# ── fakes for the real (non-mock) CloudWatch/elbv2 path ─────────────────────────

class _FakeCloudWatch:
    def __init__(self, responses=None, error=None):
        self.calls = []
        self._responses = list(responses or [])
        self._error = error

    def get_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if self._responses:
            return self._responses.pop(0)
        return {"MetricDataResults": []}


class _FakeElbv2:
    def __init__(self, lb_dns=None, lb_arn=None, target_groups=None, tags=None):
        self.lb_dns = lb_dns
        self.lb_arn = lb_arn
        self.target_groups = target_groups or []
        self.tags = tags or {}

    def describe_load_balancers(self, **kwargs):
        if kwargs.get("Marker") or not self.lb_arn:
            return {"LoadBalancers": []}
        return {"LoadBalancers": [{"LoadBalancerArn": self.lb_arn, "DNSName": self.lb_dns}]}

    def describe_target_groups(self, **kwargs):
        return {"TargetGroups": [{"TargetGroupArn": arn} for arn in self.target_groups]}

    def describe_tags(self, **kwargs):
        arns = kwargs.get("ResourceArns", [])
        return {
            "TagDescriptions": [
                {"ResourceArn": arn, "Tags": [{"Key": k, "Value": v} for k, v in self.tags.get(arn, {}).items()]}
                for arn in arns
            ]
        }


class _FakeSession:
    def __init__(self, clients: dict):
        self._clients = clients

    def client(self, name, config=None):
        return self._clients[name]


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "GetMetricData")


def _real_infra(app_stack, monkeypatch, session):
    """Flip a make_stack() result to the real (non-mock) code path and stub the
    session create_boto3_session returns."""
    import aws.session as aws_session_mod

    prod = SimpleNamespace(mode="prod")
    monkeypatch.setattr(metrics_mod, "app_config", prod)
    monkeypatch.setattr(aws_session_mod, "app_config", prod)
    app_stack.infra.is_mock = False
    app_stack.infra.save(update_fields=["is_mock"])
    monkeypatch.setattr(metrics_mod, "create_boto3_session", lambda infra, config=None: session)


# ── 1: owner-only authz ladder (delegated to api/services/owner_only.py) ────────

@pytest.mark.django_db
def test_stranger_gets_404(factory, make_stack, make_user):
    stack = make_stack()
    resp = _get(factory, make_user(), str(stack.app.id))
    assert resp.status_code == 404
    assert resp.data["code"] == "not_found"


@pytest.mark.django_db
def test_invited_admin_gets_403(factory, make_stack, make_user):
    from api.models.infrastructure_user_role import InfrastructureUserRole

    stack = make_stack()
    admin = make_user()
    stack.infra.invited_users.add(admin)
    InfrastructureUserRole.objects.create(infrastructure=stack.infra, user=admin, role=UserRole.ADMIN)

    resp = _get(factory, admin, str(stack.app.id))

    assert resp.status_code == 403
    assert resp.data["code"] == "forbidden"


@pytest.mark.django_db
def test_owner_gets_200(factory, make_stack):
    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200
    assert resp.data["range"] == "1h"
    assert resp.data["cached"] is False
    assert set(resp.data["series"]) == set(metrics_mod.SERIES_KEYS)


@pytest.mark.django_db
def test_not_yet_deployed_app_gets_409(factory, make_stack):
    stack = make_stack()
    stack.app.service_arn = None
    stack.app.save(update_fields=["service_arn"])

    resp = _get(factory, stack.owner, str(stack.app.id))

    assert resp.status_code == 409
    assert resp.data["code"] == "not_deployed"


# ── 2: range validation ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_unknown_range_is_rejected_with_400(factory, make_stack):
    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id), range="3h")
    assert resp.status_code == 400
    assert resp.data["code"] == "invalid_range"


@pytest.mark.django_db
def test_default_range_is_1h(factory, make_stack):
    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200
    assert resp.data["range"] == "1h"
    assert resp.data["period_seconds"] == 60


@pytest.mark.parametrize("time_range,period", [("1h", 60), ("6h", 300), ("24h", 300), ("7d", 3600)])
@pytest.mark.django_db
def test_period_mapping_per_range(factory, make_stack, time_range, period):
    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id), range=time_range)
    assert resp.status_code == 200
    assert resp.data["period_seconds"] == period


def test_service_rejects_unknown_range_directly():
    with pytest.raises(ValueError):
        AppMetricsService().get_metrics(user_id=uuid.uuid4(), app_id=uuid.uuid4(), time_range="3h")


# ── 3: mock/dev synthetic series ─────────────────────────────────────────────

@pytest.mark.django_db
def test_mock_ecs_populates_every_series(factory, make_stack):
    stack = make_stack()
    resp = _get(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200
    for key in metrics_mod.SERIES_KEYS:
        assert len(resp.data["series"][key]) > 0
    assert resp.data["unavailable"] == {}


@pytest.mark.django_db
def test_mock_eks_marks_cpu_and_memory_unavailable(factory, make_stack):
    stack = make_stack(compute_type="eks")
    resp = _get(factory, stack.owner, str(stack.app.id))
    assert resp.status_code == 200
    assert resp.data["series"]["cpu_percent"] == []
    assert resp.data["series"]["memory_percent"] == []
    assert resp.data["unavailable"]["cpu_percent"] == "eks_container_metrics_not_enabled"
    assert resp.data["unavailable"]["memory_percent"] == "eks_container_metrics_not_enabled"
    assert len(resp.data["series"]["request_count"]) > 0


# ── 4: cache hit ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_second_call_within_ttl_is_a_cache_hit(factory, make_stack, monkeypatch):
    calls = []
    original = AppMetricsService._synthetic_metrics

    def spy(self, *a, **kw):
        calls.append(1)
        return original(self, *a, **kw)

    monkeypatch.setattr(AppMetricsService, "_synthetic_metrics", spy)
    stack = make_stack()

    first = _get(factory, stack.owner, str(stack.app.id))
    second = _get(factory, stack.owner, str(stack.app.id))

    assert first.status_code == 200 and first.data["cached"] is False
    assert second.status_code == 200 and second.data["cached"] is True
    assert len(calls) == 1


@pytest.mark.django_db
def test_different_range_is_not_a_cache_hit(factory, make_stack):
    stack = make_stack()
    first = _get(factory, stack.owner, str(stack.app.id), range="1h")
    second = _get(factory, stack.owner, str(stack.app.id), range="6h")
    assert first.data["cached"] is False
    assert second.data["cached"] is False


@pytest.mark.django_db
def test_malformed_cache_entry_is_a_cache_miss_not_a_500(factory, make_stack, fake_cache):
    """A stale/unexpected cache payload shape must fail open (recompute), the same
    posture as a Redis error, never bubble up as a 500 for the rest of the TTL."""
    stack = make_stack()
    fake_cache.store[metrics_mod._cache_key(stack.app.id, "1h")] = '{"unexpected": "shape"}'

    resp = _get(factory, stack.owner, str(stack.app.id), range="1h")

    assert resp.status_code == 200
    assert resp.data["cached"] is False


# ── 5: real-path query construction (ECS) ────────────────────────────────────

@pytest.mark.django_db
def test_ecs_query_construction_and_dimensions(make_stack, monkeypatch):
    stack = make_stack()
    fake_cw = _FakeCloudWatch(responses=[{"MetricDataResults": []}])
    _real_infra(stack, monkeypatch, _FakeSession({"cloudwatch": fake_cw}))

    AppMetricsService().get_metrics(user_id=stack.owner.id, app_id=str(stack.app.id), time_range="6h")

    assert len(fake_cw.calls) == 1
    queries = fake_cw.calls[0]["MetricDataQueries"]
    ids = {q["Id"] for q in queries}
    assert ids == set(metrics_mod.SERIES_KEYS)
    assert fake_cw.calls[0]["ScanBy"] == "TimestampAscending"

    by_id = {q["Id"]: q for q in queries}
    cluster_name = stack.env.cluster_arn.rsplit("/", 1)[-1]
    service_name = stack.app.service_arn.rsplit("/", 1)[-1]
    cpu = by_id["cpu_percent"]
    assert cpu["MetricStat"]["Metric"]["Namespace"] == "AWS/ECS"
    assert cpu["MetricStat"]["Metric"]["MetricName"] == "CPUUtilization"
    assert cpu["MetricStat"]["Stat"] == "Average"
    assert cpu["MetricStat"]["Period"] == 300  # 6h -> 300s
    dims = {d["Name"]: d["Value"] for d in cpu["MetricStat"]["Metric"]["Dimensions"]}
    assert dims == {"ClusterName": cluster_name, "ServiceName": service_name}

    req_count = by_id["request_count"]
    assert req_count["MetricStat"]["Metric"]["Namespace"] == "AWS/ApplicationELB"
    assert req_count["MetricStat"]["Stat"] == "Sum"
    alb_dims = {d["Name"]: d["Value"] for d in req_count["MetricStat"]["Metric"]["Dimensions"]}
    assert alb_dims["LoadBalancer"] == "app/lb/abc123"
    assert alb_dims["TargetGroup"] == "targetgroup/tg/def456"

    p50 = by_id["latency_p50_ms"]
    p95 = by_id["latency_p95_ms"]
    assert p50["MetricStat"]["Metric"]["MetricName"] == "TargetResponseTime"
    assert p50["MetricStat"]["Stat"] == "p50"
    assert p95["MetricStat"]["Stat"] == "p95"


@pytest.mark.django_db
def test_target_response_time_converted_to_milliseconds(make_stack, monkeypatch):
    stack = make_stack()
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    response = {
        "MetricDataResults": [
            {"Id": "latency_p50_ms", "Timestamps": [now], "Values": [0.25]},
            {"Id": "latency_p95_ms", "Timestamps": [now], "Values": [0.5]},
        ]
    }
    fake_cw = _FakeCloudWatch(responses=[response])
    _real_infra(stack, monkeypatch, _FakeSession({"cloudwatch": fake_cw}))

    result = AppMetricsService().get_metrics(user_id=stack.owner.id, app_id=str(stack.app.id), time_range="1h")

    p50_points = result.series["latency_p50_ms"]
    p95_points = result.series["latency_p95_ms"]
    assert p50_points[0]["v"] == 250.0
    assert p95_points[0]["v"] == 500.0


@pytest.mark.django_db
def test_missing_target_group_arn_marks_alb_series_unavailable_but_keeps_cpu_mem(make_stack, monkeypatch):
    stack = make_stack()
    stack.app.target_group_arn = None
    stack.app.save(update_fields=["target_group_arn"])
    fake_cw = _FakeCloudWatch(responses=[{"MetricDataResults": []}])
    _real_infra(stack, monkeypatch, _FakeSession({"cloudwatch": fake_cw}))

    result = AppMetricsService().get_metrics(user_id=stack.owner.id, app_id=str(stack.app.id), time_range="1h")

    ids = {q["Id"] for q in fake_cw.calls[0]["MetricDataQueries"]}
    assert ids == set(metrics_mod.CPU_MEM_KEYS)
    for key in metrics_mod.ALB_KEYS:
        assert result.unavailable[key] == "target_group_not_found"
        assert result.series[key] == []


# ── 6: real-path error mapping ───────────────────────────────────────────────

@pytest.mark.django_db
def test_access_denied_maps_to_policy_refresh_required(make_stack, monkeypatch):
    stack = make_stack()
    fake_cw = _FakeCloudWatch(error=_client_error("AccessDenied"))
    _real_infra(stack, monkeypatch, _FakeSession({"cloudwatch": fake_cw}))

    resp = _get(factory=APIRequestFactory(), user=stack.owner, app_id=str(stack.app.id))

    assert resp.status_code == 422
    assert resp.data["code"] == "policy_refresh_required"
    assert "cloudwatch:GetMetricData" in resp.data["denied_actions"]


@pytest.mark.django_db
def test_throttling_maps_to_503(make_stack, monkeypatch):
    stack = make_stack()
    fake_cw = _FakeCloudWatch(error=_client_error("ThrottlingException"))
    _real_infra(stack, monkeypatch, _FakeSession({"cloudwatch": fake_cw}))

    resp = _get(factory=APIRequestFactory(), user=stack.owner, app_id=str(stack.app.id))

    assert resp.status_code == 503
    assert resp.data["code"] == "metrics_unavailable"


# ── 7: EKS target-group discovery via elbv2 tags ─────────────────────────────

@pytest.mark.django_db
def test_eks_discovers_target_group_by_controller_tags(make_stack, monkeypatch):
    stack = make_stack(compute_type="eks")
    lb_arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT_ID}:loadbalancer/app/shared-alb/xyz"
    tg_arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT_ID}:targetgroup/k8s-appapi-abcdef/123"
    fake_elbv2 = _FakeElbv2(
        lb_dns=stack.env.alb_dns, lb_arn=lb_arn, target_groups=[tg_arn],
        tags={tg_arn: {"ingress.k8s.aws/resource": "app-api/api-api:80", "elbv2.k8s.aws/cluster": "infra"}},
    )
    fake_cw = _FakeCloudWatch(responses=[{"MetricDataResults": []}])
    _real_infra(stack, monkeypatch, _FakeSession({"cloudwatch": fake_cw, "elbv2": fake_elbv2}))

    AppMetricsService().get_metrics(user_id=stack.owner.id, app_id=str(stack.app.id), time_range="1h")

    queries = fake_cw.calls[0]["MetricDataQueries"]
    by_id = {q["Id"]: q for q in queries}
    dims = {d["Name"]: d["Value"] for d in by_id["request_count"]["MetricStat"]["Metric"]["Dimensions"]}
    assert dims["LoadBalancer"] == "app/shared-alb/xyz"
    assert dims["TargetGroup"] == "targetgroup/k8s-appapi-abcdef/123"


@pytest.mark.django_db
def test_eks_no_target_group_match_marks_alb_series_unavailable(make_stack, monkeypatch):
    stack = make_stack(compute_type="eks")
    lb_arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT_ID}:loadbalancer/app/shared-alb/xyz"
    fake_elbv2 = _FakeElbv2(lb_dns=stack.env.alb_dns, lb_arn=lb_arn, target_groups=[], tags={})
    fake_cw = _FakeCloudWatch()
    _real_infra(stack, monkeypatch, _FakeSession({"cloudwatch": fake_cw, "elbv2": fake_elbv2}))

    result = AppMetricsService().get_metrics(user_id=stack.owner.id, app_id=str(stack.app.id), time_range="1h")

    assert fake_cw.calls == []
    for key in metrics_mod.ALB_KEYS:
        assert result.unavailable[key] == "target_group_not_found"
    for key in metrics_mod.CPU_MEM_KEYS:
        assert result.unavailable[key] == "eks_container_metrics_not_enabled"


def test_alb_dimension_values_match_cloudwatch_format():
    """Real AWS: CloudWatch lists TargetGroup=targetgroup/<name>/<id> and
    LoadBalancer=app/<name>/<id>. The prefix-stripped TG value matched no metrics."""
    from api.services.app_metrics_service import _alb_dim_value, _tg_dim_value

    assert _tg_dim_value(
        "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/dummy-backend-e3a4b290-tg/c878730344e93648"
    ) == "targetgroup/dummy-backend-e3a4b290-tg/c878730344e93648"
    assert _alb_dim_value(
        "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/infra-alb/f7d156009cc5f64a"
    ) == "app/infra-alb/f7d156009cc5f64a"

