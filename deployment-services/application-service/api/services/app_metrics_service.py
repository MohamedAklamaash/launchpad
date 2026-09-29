"""Per-app metrics (dashboard charts): CPU/memory (ECS only — EKS Auto Mode has no
Container Insights), ALB request/latency/error/health series for both compute types.
Reads on demand through the same assumed-role session api/services/runtime_logs_service.py
uses — nothing is stored on the platform beyond a 30s Redis cache of the rendered
response, keyed by (app_id, range).

One `cloudwatch.get_metric_data` call per request: every series is a MetricDataQuery in
the same GetMetricData call, never a series-per-call loop.
"""
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import redis
from aws.session import create_boto3_session, gate_mismatch
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError
from django.conf import settings
from shared.enums.orchestrator import ComputeType
from shared.mode import is_dev_mode

from api.common.envs.application import app_config
from api.common.naming import require_k8s_safe_slug
from api.k8s.deployer import _find_group_alb_arn, namespace_for
from api.services.owner_only import (
    NotDeployedError,
    get_deployed_environment,
    get_owner_authorized_app,
)
from api.services.policy_errors import PolicyRefreshRequiredError

logger = logging.getLogger(__name__)

CALL_DEADLINE_SECONDS = 6.0
BUDGET_CONNECT_TIMEOUT = 2
BUDGET_MAX_READ_TIMEOUT = 4.0
BUDGET_MIN_READ_TIMEOUT = 1.0
CACHE_TTL_SECONDS = 30
MAX_TAG_BATCH = 20  # elbv2 DescribeTags accepts at most 20 ResourceArns per call.

# range -> (window seconds, period seconds). Contract: 1h->60s, 6h/24h->300s, 7d->3600s.
RANGE_SPECS = {
    "1h": (3600, 60),
    "6h": (21600, 300),
    "24h": (86400, 300),
    "7d": (604800, 3600),
}
DEFAULT_RANGE = "1h"

CPU_MEM_KEYS = ("cpu_percent", "memory_percent")
ALB_KEYS = (
    "request_count", "latency_p50_ms", "latency_p95_ms",
    "http_4xx", "http_5xx", "healthy_targets",
)
SERIES_KEYS = CPU_MEM_KEYS + ALB_KEYS

EKS_CPU_MEM_UNAVAILABLE = "eks_container_metrics_not_enabled"

_ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException"}
_RETRY_LATER_CODES = {
    "Throttling", "ThrottlingException", "RequestLimitExceeded", "TooManyRequestsException",
}


class MetricsUnavailableError(Exception):
    """CloudWatch signaled a transient condition (throttling, timeout) — 503, retry later."""


def _budget_config(deadline: float) -> Config:
    remaining = deadline - time.monotonic()
    read_timeout = max(BUDGET_MIN_READ_TIMEOUT, min(BUDGET_MAX_READ_TIMEOUT, remaining))
    max_attempts = 1 if remaining < BUDGET_MAX_READ_TIMEOUT else 2
    return Config(
        connect_timeout=BUDGET_CONNECT_TIMEOUT, read_timeout=read_timeout,
        retries={"max_attempts": max_attempts, "mode": "standard"},
    )


def _check_deadline(deadline: float):
    if time.monotonic() > deadline:
        raise MetricsUnavailableError("Timed out contacting the customer's account")


@dataclass
class MetricsResult:
    time_range: str
    period_seconds: int
    compute_type: str
    generated_at: str
    series: dict = field(default_factory=dict)
    unavailable: dict = field(default_factory=dict)
    cached: bool = False

    def to_dict(self) -> dict:
        return {
            "range": self.time_range,
            "period_seconds": self.period_seconds,
            "compute_type": self.compute_type,
            "generated_at": self.generated_at,
            "cached": self.cached,
            "series": self.series,
            "unavailable": self.unavailable,
        }


# ── Redis cache (mirrors api/services/deployment_lock.py's own pool — no Django CACHES
# backend is configured in this service) ────────────────────────────────────────────

_pool = redis.ConnectionPool(
    host=settings.REDIS_HOST, port=settings.REDIS_PORT, password=settings.REDIS_PASSWORD,
    db=settings.REDIS_DB, decode_responses=True, max_connections=20,
    socket_timeout=2, socket_connect_timeout=2, retry_on_timeout=True,
)


def _cache_key(app_id, time_range: str) -> str:
    return f"metrics:{app_id}:{time_range}"


def _cache_get(app_id, time_range: str) -> dict | None:
    try:
        raw = redis.Redis(connection_pool=_pool).get(_cache_key(app_id, time_range))
    except redis.RedisError:
        logger.warning("app_metrics cache read failed; falling through to a live query", exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _cache_set(app_id, time_range: str, payload: dict) -> None:
    # Fail open: a cache write failure must not turn a successful, already-computed
    # response into an error — the next request just misses and recomputes.
    try:
        redis.Redis(connection_pool=_pool).setex(
            _cache_key(app_id, time_range), CACHE_TTL_SECONDS, json.dumps(payload),
        )
    except redis.RedisError:
        logger.warning("app_metrics cache write failed", exc_info=True)


def _aws_error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "Unknown")


class AppMetricsService:
    def get_metrics(self, *, user_id, app_id, time_range: str) -> MetricsResult:
        if time_range not in RANGE_SPECS:
            raise ValueError(f"range must be one of {sorted(RANGE_SPECS)}")

        app = get_owner_authorized_app(user_id, app_id)
        infra = app.infrastructure

        cached = _cache_get(app.id, time_range)
        if cached is not None:
            try:
                return MetricsResult(
                    time_range=cached["range"], period_seconds=cached["period_seconds"],
                    compute_type=cached["compute_type"], generated_at=cached["generated_at"],
                    series=cached["series"], unavailable=cached["unavailable"], cached=True,
                )
            except KeyError:
                # Fail open, same posture as a Redis read/write error: a stale or
                # unexpected cache payload shape must not turn an otherwise-successful
                # request into a 500 for the rest of the TTL — just recompute.
                logger.warning("app_metrics cache entry had an unexpected shape; recomputing", exc_info=True)

        if gate_mismatch(infra):
            raise MetricsUnavailableError("AWS access is misconfigured for this infrastructure")
        if not bool(getattr(infra, "is_mock", False)) and not infra.code:
            raise NotDeployedError("Infrastructure not authenticated with AWS")

        env = get_deployed_environment(app, infra)
        window_seconds, period = RANGE_SPECS[time_range]
        end = _floor_to_period(datetime.now(timezone.utc), period)
        start = end - timedelta(seconds=window_seconds)

        if is_dev_mode(app_config.mode) or infra.is_mock:
            result = self._synthetic_metrics(app, infra, time_range, period, start, end)
        else:
            deadline = time.monotonic() + CALL_DEADLINE_SECONDS
            session = create_boto3_session(infra, config=_budget_config(deadline))
            if infra.compute_type == ComputeType.EKS:
                result = self._eks_metrics(session, app, env, infra, time_range, period, start, end, deadline)
            else:
                result = self._ecs_metrics(session, app, env, infra, time_range, period, start, end, deadline)

        _cache_set(app.id, time_range, result.to_dict())
        return result

    # ── ECS ──────────────────────────────────────────────────────────────────

    def _ecs_metrics(self, session, app, env, infra, time_range, period, start, end, deadline) -> MetricsResult:
        cluster_name = env.cluster_arn.rsplit("/", 1)[-1]
        service_name = app.service_arn.rsplit("/", 1)[-1] if app.service_arn else None
        ecs_dims = [
            {"Name": "ClusterName", "Value": cluster_name},
            {"Name": "ServiceName", "Value": service_name},
        ]

        lb_dim = _alb_dim_value(env.alb_arn)
        tg_dim = _tg_dim_value(app.target_group_arn)
        unavailable = {}
        if lb_dim is None:
            unavailable.update({k: "load_balancer_not_found" for k in ALB_KEYS})
        elif tg_dim is None:
            unavailable.update({k: "target_group_not_found" for k in ALB_KEYS})
        alb_dims = [{"Name": "LoadBalancer", "Value": lb_dim}, {"Name": "TargetGroup", "Value": tg_dim}] \
            if lb_dim and tg_dim else None

        queries = _cpu_mem_queries(ecs_dims, period)
        if alb_dims:
            queries += _alb_queries(alb_dims, period)

        _check_deadline(deadline)
        cloudwatch = session.client("cloudwatch", config=_budget_config(deadline))
        series = self._run_queries(cloudwatch, queries, start, end, deadline)

        return _build_result(time_range, period, infra.compute_type, series, unavailable)

    # ── EKS ──────────────────────────────────────────────────────────────────

    def _eks_metrics(self, session, app, env, infra, time_range, period, start, end, deadline) -> MetricsResult:
        unavailable = {k: EKS_CPU_MEM_UNAVAILABLE for k in CPU_MEM_KEYS}
        slug = require_k8s_safe_slug(app.name)
        namespace = namespace_for(slug)

        alb_dims = self._discover_eks_target_group(session, env, namespace, slug, deadline)
        if alb_dims is None:
            unavailable.update({k: "target_group_not_found" for k in ALB_KEYS})
        queries = _alb_queries(alb_dims, period) if alb_dims else []

        series = {}
        if queries:
            _check_deadline(deadline)
            cloudwatch = session.client("cloudwatch", config=_budget_config(deadline))
            series = self._run_queries(cloudwatch, queries, start, end, deadline)

        return _build_result(time_range, period, infra.compute_type, series, unavailable)

    def _discover_eks_target_group(self, session, env, namespace, slug, deadline):
        """Best-effort discovery of the target group the AWS Load Balancer Controller
        created for this app's Ingress (see api/k8s/deployer.py's INGRESS_CLASS_NAME /
        _ingress_manifest — every app's Ingress shares one ALB via a group name set on
        the cluster's IngressClassParams). The controller has no API to look up "the
        target group for Ingress X" directly, so this mirrors
        EKSDeployer._verify_eks_https_listener's own approach: find the shared ALB from
        its known DNS name, list its target groups, then match by the controller's own
        tags.

        UNVERIFIED against a real cluster (see plan/REAL-AWS-VALIDATION.md): the exact
        `ingress.k8s.aws/resource` value the controller writes is undocumented beyond
        "derived from namespace/ingress/service/port", so this matches on the
        `<namespace>/<slug>` prefix rather than an exact string, and prefers a target
        group whose tag does not contain `-host-` (the path Ingress's backend, since a
        host-only Ingress's own health-check hits the same nginx sidecar) when both the
        path and host Ingresses have their own target groups. Returns None (all ALB
        series → unavailable) if the ALB isn't discoverable or no target group matches.
        """
        if not env.alb_dns:
            return None
        try:
            elbv2 = session.client("elbv2", config=_budget_config(deadline))
            lb_arn = _find_group_alb_arn(elbv2, env.alb_dns)
            if lb_arn is None:
                return None
            _check_deadline(deadline)
            tg_arns = self._target_groups_for_lb(elbv2, lb_arn)
            if not tg_arns:
                return None
            _check_deadline(deadline)
            match = self._match_target_group_by_tags(elbv2, tg_arns, namespace, slug)
        except ClientError:
            logger.warning("EKS target group discovery failed for %s", slug, exc_info=True)
            return None
        if match is None:
            return None
        return [
            {"Name": "LoadBalancer", "Value": _alb_dim_value(lb_arn)},
            {"Name": "TargetGroup", "Value": _tg_dim_value(match)},
        ]

    def _target_groups_for_lb(self, elbv2, lb_arn: str) -> list:
        arns = []
        marker = None
        while True:
            kwargs = {"LoadBalancerArn": lb_arn}
            if marker:
                kwargs["Marker"] = marker
            response = elbv2.describe_target_groups(**kwargs)
            arns += [tg["TargetGroupArn"] for tg in response.get("TargetGroups", [])]
            marker = response.get("NextMarker")
            if not marker:
                return arns

    def _match_target_group_by_tags(self, elbv2, tg_arns: list, namespace: str, slug: str) -> str | None:
        prefix = f"{namespace}/{slug}"
        candidates = []
        for i in range(0, len(tg_arns), MAX_TAG_BATCH):
            batch = tg_arns[i:i + MAX_TAG_BATCH]
            response = elbv2.describe_tags(ResourceArns=batch)
            for description in response.get("TagDescriptions", []):
                tags = {t["Key"]: t["Value"] for t in description.get("Tags", [])}
                resource = tags.get("ingress.k8s.aws/resource", "")
                if resource.startswith(prefix):
                    candidates.append((resource, description["ResourceArn"]))
        if not candidates:
            return None
        # Prefer the path Ingress's own target group over the host-only Ingress's, when
        # both exist — see _discover_eks_target_group's docstring.
        non_host = [arn for resource, arn in candidates if "-host-" not in resource]
        return non_host[0] if non_host else candidates[0][1]

    # ── shared CloudWatch call + response shaping ───────────────────────────────

    def _run_queries(self, cloudwatch, queries: list, start, end, deadline) -> dict:
        if not queries:
            return {}
        series = {key: [] for key in {q["Id"] for q in queries}}
        next_token = None
        while True:
            _check_deadline(deadline)
            kwargs = {
                "MetricDataQueries": queries, "StartTime": start, "EndTime": end,
                "ScanBy": "TimestampAscending",
            }
            if next_token:
                kwargs["NextToken"] = next_token
            try:
                response = cloudwatch.get_metric_data(**kwargs)
            except (ConnectTimeoutError, ReadTimeoutError) as e:
                raise MetricsUnavailableError("Timed out contacting the customer's account") from e
            except ClientError as e:
                code = _aws_error_code(e)
                if code in _ACCESS_DENIED_CODES:
                    raise PolicyRefreshRequiredError(
                        "Launchpad's IAM role in your AWS account is missing CloudWatch metrics "
                        "permissions. Re-run the refresh policy script and try again.",
                        denied_actions=["cloudwatch:GetMetricData"],
                    ) from e
                if code in _RETRY_LATER_CODES:
                    raise MetricsUnavailableError(f"CloudWatch is temporarily unable to serve this request ({code})") from e
                raise MetricsUnavailableError(f"CloudWatch call failed ({code})") from e

            for result in response.get("MetricDataResults", []):
                key = result["Id"]
                for ts, value in zip(result.get("Timestamps", []), result.get("Values", [])):
                    series[key].append((ts, value))
            next_token = response.get("NextToken")
            if not next_token:
                break

        return {key: _finalize_points(key, points) for key, points in series.items()}

    # ── mock / dev ───────────────────────────────────────────────────────────

    def _synthetic_metrics(self, app, infra, time_range, period, start, end) -> MetricsResult:
        """Deterministic fake series for is_mock infras / MODE=dev — never touches AWS.
        Derived from the full app id (never a slice — CLAUDE.md), so the same app+range
        always renders the same chart without a real deploy."""
        unavailable = {}
        if infra.compute_type == ComputeType.EKS:
            unavailable = {k: EKS_CPU_MEM_UNAVAILABLE for k in CPU_MEM_KEYS}

        timestamps = _timestamps(start, end, period)
        seed = int(hashlib.sha256(f"{app.id}:{time_range}".encode()).hexdigest()[:8], 16)
        series = {}
        for key in SERIES_KEYS:
            if key in unavailable:
                series[key] = []
                continue
            series[key] = [
                {"t": ts.isoformat(), "v": _synthetic_value(key, seed, i)}
                for i, ts in enumerate(timestamps)
            ]
        return _build_result(time_range, period, infra.compute_type, series, unavailable)


def _floor_to_period(dt: datetime, period: int) -> datetime:
    epoch_seconds = int(dt.timestamp())
    return datetime.fromtimestamp(epoch_seconds - (epoch_seconds % period), tz=timezone.utc)


def _timestamps(start: datetime, end: datetime, period: int) -> list:
    points = []
    current = start
    while current < end:
        points.append(current)
        current += timedelta(seconds=period)
    return points


def _synthetic_value(key: str, seed: int, index: int) -> float:
    point_seed = (seed + index * 2654435761) & 0xFFFFFFFF
    wave = (math.sin(index / 5.0) + 1) / 2  # 0..1, smooth over time for a readable chart
    noise = (point_seed % 1000) / 1000.0
    magnitude = {
        "cpu_percent": 80.0, "memory_percent": 75.0, "request_count": 500.0,
        "latency_p50_ms": 120.0, "latency_p95_ms": 300.0,
        "http_4xx": 5.0, "http_5xx": 2.0, "healthy_targets": 2.0,
    }[key]
    return round(magnitude * (0.3 + 0.5 * wave) * (0.7 + 0.3 * noise), 2)


def _alb_dim_value(arn: str | None) -> str | None:
    if not arn:
        return None
    return arn.split(":loadbalancer/", 1)[-1] if ":loadbalancer/" in arn else None


def _tg_dim_value(arn: str | None) -> str | None:
    if not arn:
        return None
    return arn.split(":targetgroup/", 1)[-1] if ":targetgroup/" in arn else None


def _cpu_mem_queries(dims: list, period: int) -> list:
    return [
        _metric_query("cpu_percent", "AWS/ECS", "CPUUtilization", dims, "Average", period),
        _metric_query("memory_percent", "AWS/ECS", "MemoryUtilization", dims, "Average", period),
    ]


def _alb_queries(dims: list, period: int) -> list:
    return [
        _metric_query("request_count", "AWS/ApplicationELB", "RequestCount", dims, "Sum", period),
        _metric_query("latency_p50_ms", "AWS/ApplicationELB", "TargetResponseTime", dims, "p50", period),
        _metric_query("latency_p95_ms", "AWS/ApplicationELB", "TargetResponseTime", dims, "p95", period),
        _metric_query("http_4xx", "AWS/ApplicationELB", "HTTPCode_Target_4XX_Count", dims, "Sum", period),
        _metric_query("http_5xx", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", dims, "Sum", period),
        _metric_query("healthy_targets", "AWS/ApplicationELB", "HealthyHostCount", dims, "Average", period),
    ]


def _metric_query(id_: str, namespace: str, metric_name: str, dims: list, stat: str, period: int) -> dict:
    return {
        "Id": id_,
        "MetricStat": {
            "Metric": {"Namespace": namespace, "MetricName": metric_name, "Dimensions": dims},
            "Period": period,
            "Stat": stat,
        },
        "ReturnData": True,
    }


_MS_SERIES = {"latency_p50_ms", "latency_p95_ms"}


def _finalize_points(key: str, points: list) -> list:
    points = sorted(points, key=lambda p: p[0])
    scale = 1000.0 if key in _MS_SERIES else 1.0
    return [
        {"t": ts.astimezone(timezone.utc).isoformat(), "v": round(value * scale, 2)}
        for ts, value in points
    ]


def _build_result(time_range, period, compute_type, series: dict, unavailable: dict) -> MetricsResult:
    full_series = {key: series.get(key, []) for key in SERIES_KEYS}
    return MetricsResult(
        time_range=time_range, period_seconds=period, compute_type=compute_type,
        generated_at=datetime.now(timezone.utc).isoformat(), series=full_series,
        unavailable=unavailable,
    )
