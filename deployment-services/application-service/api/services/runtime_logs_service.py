"""On-demand runtime log tail (F2): reads the customer's own CloudWatch Logs (ECS) or pod
logs (EKS) through the existing assumed-role session. Nothing is stored on the platform —
the caller is responsible for writing the metadata-only audit row; this module returns log
content and never logs it itself.

B1: every stream/pod read is derived from THIS application's own ECS service or EKS
namespace — never from a request parameter — so two applications that happen to share an
account+region (or a renamed/recreated app) can never read each other's log history.
"""
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from aws.session import create_boto3_session
from botocore.config import Config
from botocore.exceptions import ClientError
from kubernetes.client.rest import ApiException
from shared.enums.orchestrator import ComputeType
from shared.enums.user_role import UserRole

from api.common.naming import app_slug, ecs_log_group, require_k8s_safe_slug
from api.k8s.deployer import cluster_name_from_arn, k8s_apis, namespace_for
from api.models.application import Application
from api.models.environment import Environment
from api.services.infrastructure_permissions import InfrastructurePermissions
from api.services.runtime_logs_cursor import decode_cursor, encode_cursor

logger = logging.getLogger(__name__)

MIN_WINDOW_MINUTES = 1
DEFAULT_WINDOW_MINUTES = 15
MAX_WINDOW_MINUTES = 60
MAX_EVENTS_PER_PAGE = 100
MAX_MESSAGE_BYTES = 4096
MAX_TOTAL_BYTES = 256 * 1024
MAX_ECS_TASKS = 10
MAX_EKS_PODS = 5
CALL_DEADLINE_SECONDS = 6.0
TRUNCATION_MARKER = "...[truncated]"
CONTAINERS = ("app", "proxy")

# Short, non-adaptive timeouts distinct from aws.session.BOTO3_CONFIG: this endpoint sits
# behind the gateway's 10s proxy timeout and each call is on the request's critical path,
# so it cannot afford that client's 60s read timeout / adaptive retries.
RUNTIME_LOGS_BOTO_CONFIG = Config(
    connect_timeout=2, read_timeout=4, retries={"max_attempts": 2, "mode": "standard"},
)


class NotDeployedError(Exception):
    """The application has no active deployment to read logs from."""


class UpstreamError(Exception):
    """A CloudWatch/ECS/k8s call failed or the per-request deadline was exceeded."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class LogsResult:
    events: list
    next_cursor: str | None
    container: str
    compute_type: str
    infra_id: uuid.UUID
    window_start_ms: int | None
    window_end_ms: int | None
    event_count: int
    total_bytes: int
    truncated: bool


class RuntimeLogsService:
    def get_logs(self, *, user_id, app_id, container: str, minutes, cursor, previous: bool) -> LogsResult:
        if container not in CONTAINERS:
            raise ValueError("container must be 'app' or 'proxy'")
        if cursor and minutes is not None:
            raise ValueError("cursor and minutes cannot be combined")

        app = self._get_authorized_app(user_id, app_id)
        infra = app.infrastructure
        is_eks = infra.compute_type == ComputeType.EKS

        # Validated before create_boto3_session: for a real (non-mock) infra that call
        # performs the AssumeRole immediately (RefreshableCredentials fetches its first
        # token eagerly), so a param combination that's going to 400 anyway must not
        # spend one.
        if is_eks and cursor:
            raise ValueError("cursor is not supported for Kubernetes applications")
        if not is_eks and previous:
            raise ValueError("previous is only supported for Kubernetes applications")

        env = self._deployed_environment(app, infra)

        deadline = time.monotonic() + CALL_DEADLINE_SECONDS
        session = create_boto3_session(infra)

        if is_eks:
            return self._tail_eks(session, app, env, infra, container, minutes, previous, deadline)
        return self._tail_ecs(session, app, env, infra, container, minutes, cursor, user_id, deadline)

    # ── authorization ────────────────────────────────────────────────────────

    def _get_authorized_app(self, user_id, app_id) -> Application:
        try:
            uuid.UUID(str(app_id))
        except ValueError:
            raise LookupError("Application not found") from None
        try:
            app = Application.objects.select_related("infrastructure").get(id=app_id)
        except Application.DoesNotExist:
            raise LookupError("Application not found") from None
        role = InfrastructurePermissions.get_user_role(app.infrastructure, user_id)
        if role is None:
            raise LookupError("Application not found")
        if role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only the infrastructure owner can view runtime logs")
        return app

    def _deployed_environment(self, app: Application, infra) -> Environment:
        env = Environment.objects.filter(infrastructure_id=infra.id).first()
        if not env or env.status != "ACTIVE" or not env.cluster_arn:
            raise NotDeployedError("Environment is not active")
        is_eks = infra.compute_type == ComputeType.EKS
        deployed = (app.runtime_refs or {}).get("namespace") if is_eks else app.service_arn
        if not deployed:
            raise NotDeployedError("Application has not been deployed")
        return env

    # ── ECS ──────────────────────────────────────────────────────────────────

    def _tail_ecs(self, session, app, env, infra, container, minutes, cursor, user_id, deadline) -> LogsResult:
        slug = app_slug(app.name)
        family = f"{slug}-task"
        log_group = ecs_log_group(slug)
        service_name = f"{slug}-service"

        if cursor:
            payload = decode_cursor(cursor, app_id=app.id, user_id=user_id, container=container)
            start_ms, end_ms, aws_token = payload["s"], payload["e"], payload["t"]
        else:
            end_ms = _now_ms()
            start_ms = end_ms - _clamp_minutes(minutes) * 60_000
            start_ms = max(start_ms, int(app.created_at.timestamp() * 1000))
            aws_token = None

        ecs = session.client("ecs", config=RUNTIME_LOGS_BOTO_CONFIG)
        task_ids = self._ecs_task_ids(ecs, env.cluster_arn, service_name, deadline)
        stream_prefix, container_name = _ecs_stream_parts(family, container)
        streams = [f"{stream_prefix}/{container_name}/{task_id}" for task_id in task_ids][:100]

        if not streams:
            return LogsResult(
                events=[], next_cursor=None, container=container, compute_type=infra.compute_type,
                infra_id=infra.id, window_start_ms=start_ms, window_end_ms=end_ms,
                event_count=0, total_bytes=0, truncated=False,
            )

        self._check_deadline(deadline)
        logs_client = session.client("logs", config=RUNTIME_LOGS_BOTO_CONFIG)
        kwargs = {
            "logGroupName": log_group, "logStreamNames": streams,
            "startTime": start_ms, "endTime": end_ms, "limit": MAX_EVENTS_PER_PAGE,
        }
        if aws_token:
            kwargs["nextToken"] = aws_token
        try:
            response = logs_client.filter_log_events(**kwargs)
        except logs_client.exceptions.ResourceNotFoundException:
            response = {"events": []}
        except ClientError as e:
            raise UpstreamError("Upstream call failed", code=_aws_error_code(e)) from e

        events, total_bytes, truncated = _cap_events(response.get("events", []))
        next_cursor = None
        next_token = response.get("nextToken")
        if next_token:
            next_cursor = encode_cursor(
                app_id=app.id, user_id=user_id, container=container,
                start_ms=start_ms, end_ms=end_ms, aws_token=next_token,
            )

        return LogsResult(
            events=events, next_cursor=next_cursor, container=container, compute_type=infra.compute_type,
            infra_id=infra.id, window_start_ms=start_ms, window_end_ms=end_ms,
            event_count=len(events), total_bytes=total_bytes, truncated=truncated,
        )

    def _ecs_task_ids(self, ecs, cluster_arn, service_name, deadline) -> list:
        task_arns = []
        for desired_status in ("RUNNING", "STOPPED"):
            self._check_deadline(deadline)
            try:
                response = ecs.list_tasks(cluster=cluster_arn, serviceName=service_name, desiredStatus=desired_status)
            except ClientError as e:
                raise UpstreamError("Upstream call failed", code=_aws_error_code(e)) from e
            task_arns.extend(response.get("taskArns", []))
        return [arn.rsplit("/", 1)[-1] for arn in task_arns][:MAX_ECS_TASKS]

    # ── EKS ──────────────────────────────────────────────────────────────────

    def _tail_eks(self, session, app, env, infra, container, minutes, previous, deadline) -> LogsResult:
        slug = require_k8s_safe_slug(app.name)
        namespace = namespace_for(slug)
        container_name = f"{slug}-app" if container == "app" else f"{slug}-nginx"
        window_seconds = _clamp_minutes(minutes) * 60
        end_ms = _now_ms()
        start_ms = end_ms - window_seconds * 1000

        events: list = []
        total_bytes = 0
        truncated = False
        with k8s_apis(session, infra, cluster_name_from_arn(env.cluster_arn)) as apis:
            self._check_deadline(deadline)
            try:
                pods = apis.core.list_namespaced_pod(namespace, label_selector=f"app={slug}").items
            except ApiException as e:
                raise UpstreamError("Upstream call failed", code=f"k8s-{e.status}") from e
            pods = pods[:MAX_EKS_PODS]
            for pod in pods:
                if total_bytes >= MAX_TOTAL_BYTES:
                    truncated = True
                    break
                self._check_deadline(deadline)
                try:
                    raw = apis.core.read_namespaced_pod_log(
                        pod.metadata.name, namespace, container=container_name,
                        tail_lines=500, limit_bytes=512 * 1024, since_seconds=window_seconds,
                        timestamps=True, previous=bool(previous), follow=False,
                        _request_timeout=(2, 4),
                    )
                except ApiException as e:
                    if e.status == 404:
                        continue
                    raise UpstreamError("Upstream call failed", code=f"k8s-{e.status}") from e
                lines, line_bytes, line_truncated = _cap_pod_log_lines(raw, MAX_TOTAL_BYTES - total_bytes)
                events.extend(lines)
                total_bytes += line_bytes
                truncated = truncated or line_truncated

        return LogsResult(
            events=events, next_cursor=None, container=container, compute_type=infra.compute_type,
            infra_id=infra.id, window_start_ms=start_ms, window_end_ms=end_ms,
            event_count=len(events), total_bytes=total_bytes, truncated=truncated,
        )

    def _check_deadline(self, deadline):
        if time.monotonic() > deadline:
            raise UpstreamError("Timed out contacting the customer's account", code="Timeout")


def _clamp_minutes(minutes) -> int:
    if minutes is None:
        return DEFAULT_WINDOW_MINUTES
    return max(MIN_WINDOW_MINUTES, min(MAX_WINDOW_MINUTES, int(minutes)))


def _now_ms() -> int:
    return int(time.time() * 1000)


_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9.]{1,64}")


def _aws_error_code(exc: ClientError) -> str:
    # AWS error codes are short, fixed identifiers (AccessDenied, ThrottlingException, …).
    # Allowlist the shape rather than passing the field through — the error body is meant
    # to carry a code, never free text, and this is what stands between an unexpected SDK
    # value and the response the client sees.
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    return code if _ERROR_CODE_RE.fullmatch(code) else "Unknown"


def _ecs_stream_parts(family: str, container: str) -> tuple:
    if container == "app":
        return "app", family
    return "nginx", f"{family}-nginx"


def _truncate_message(message: str) -> tuple:
    raw = message.encode("utf-8", "replace")
    if len(raw) <= MAX_MESSAGE_BYTES:
        return message, False
    return raw[:MAX_MESSAGE_BYTES].decode("utf-8", "ignore") + TRUNCATION_MARKER, True


def _cap_events(raw_events: list) -> tuple:
    events = []
    total_bytes = 0
    truncated = False
    for event in sorted(raw_events, key=lambda e: e.get("timestamp", 0)):
        if total_bytes >= MAX_TOTAL_BYTES:
            truncated = True
            break
        message, was_truncated = _truncate_message(event.get("message", ""))
        truncated = truncated or was_truncated
        total_bytes += len(message.encode("utf-8", "replace"))
        events.append({
            "timestamp": datetime.fromtimestamp(event.get("timestamp", 0) / 1000, tz=timezone.utc).isoformat(),
            "message": message,
        })
    return events, total_bytes, truncated


def _cap_pod_log_lines(raw: str, budget_bytes: int) -> tuple:
    lines = []
    total_bytes = 0
    truncated = False
    for line in (raw or "").splitlines():
        if total_bytes >= budget_bytes:
            truncated = True
            break
        timestamp, _, message = line.partition(" ")
        message, was_truncated = _truncate_message(message)
        truncated = truncated or was_truncated
        total_bytes += len(message.encode("utf-8", "replace"))
        lines.append({"timestamp": timestamp, "message": message})
    return lines, total_bytes, truncated
