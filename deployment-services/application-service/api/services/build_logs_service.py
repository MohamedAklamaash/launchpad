"""F7 ticket 8: on-demand log tail for a platform build. Reads the build's `init` and
`kaniko` CloudWatch streams (platform account, `infra/platform-builder/logs.tf`) through
the builder-launcher role — never the customer's own AssumeRole session, since this build
output lives on the platform side, not in the customer's account. Nothing is stored beyond
CloudWatch's own retention; the caller writes the metadata-only audit row.

Authorization is the F2 owner-only rule (`get_owner_authorized_app`: SUPER_ADMIN only, an
invited ADMIN gets 403) with none of `get_deployed_environment`'s "has this app ever
deployed" check — a first-ever build has no `service_arn`/namespace yet, and this read
never touches the customer's environment at all.

Redaction: `_sanitize_message` strips ANSI/control/bidi characters for rendering safety,
exactly like `runtime_logs_service.py`'s identically-named function (not reused directly —
see that module's docstring: it's a rendering-safety measure, not a secrets redactor, and
is kept as a short, self-contained copy here rather than a cross-module import of a private
name). On top of that, build output is a genuinely different risk surface than customer
app/request logs: it can contain Launchpad-side detail (a base image pull, BuildKit/Kaniko
internals) and, in principle, an echoed registry credential if a Dockerfile's `RUN` line
were ever careless enough to print one. `_sanitize_message`'s `_CREDENTIAL_PATTERNS` pass
denylists the shapes that would matter here — it is deliberately NOT the allowlist model
infrastructure-service's
`log_redaction.py` uses for terraform output: that model drops any line that doesn't match
a known-safe terraform shape, which would withhold nearly every line of a Dockerfile `RUN`
log. Build output has no equivalently closed structure, so denylisting specific credential
shapes is the considered trade-off here, not a default.
"""
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from aws import platform_builder
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

from api.services.build_logs_cursor import decode_cursor, encode_cursor
from api.services.owner_only import get_owner_authorized_app

CONTAINERS = (platform_builder.INIT_CONTAINER_NAME, platform_builder.KANIKO_CONTAINER_NAME)
IN_PROGRESS_STATUSES = ("BUILDING", "PUSHING_IMAGE")

DEFAULT_EVENTS_PER_STREAM = 200
MAX_EVENTS_PER_STREAM = 500
MAX_MESSAGE_BYTES = 4096
MAX_TOTAL_BYTES = 256 * 1024
TRUNCATION_MARKER = "...[truncated]"
# Matches runtime_logs_service.py's own margin under the gateway's 10s proxy timeout.
CALL_DEADLINE_SECONDS = 6.0

# Mirrors runtime_logs_service.py's BUDGET_* constants: short, non-adaptive timeouts sized
# to whatever's left of the request deadline, since this sits behind the gateway's proxy
# timeout and is polled every few seconds while a build is in progress.
BUDGET_CONNECT_TIMEOUT = 2
BUDGET_MAX_READ_TIMEOUT = 4.0
BUDGET_MIN_READ_TIMEOUT = 1.0


def _budget_config(deadline: float) -> Config:
    remaining = deadline - time.monotonic()
    read_timeout = max(BUDGET_MIN_READ_TIMEOUT, min(BUDGET_MAX_READ_TIMEOUT, remaining))
    max_attempts = 1 if remaining < BUDGET_MAX_READ_TIMEOUT else 2
    return Config(
        connect_timeout=BUDGET_CONNECT_TIMEOUT, read_timeout=read_timeout,
        retries={"max_attempts": max_attempts, "mode": "standard"},
    )


class NoBuildError(LookupError):
    """No build (of any kind) is addressable at this path — maps to 404, same as "app
    not found" a layer up: a rollback's Deployment row, a row written before build_id
    existed, or (for the in-progress path) no build currently running."""


class LegacyBuildError(Exception):
    """The build this path resolves to ran on the legacy CodeBuild path (build_id set,
    build_task_id not — no ECS task ever existed for it). Maps to 409 with a machine
    code so the frontend can distinguish "wrong path" from "nothing to show"."""

    code = "legacy_build"


class UpstreamError(Exception):
    """A CloudWatch call failed or the per-request deadline was exceeded."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class BuildLogsResult:
    events: list
    next_cursor: str | None
    complete: bool
    build_id: str
    infra_id: object
    event_count: int
    total_bytes: int


class BuildLogsService:
    def get_logs(self, *, user_id, app_id, deployment_id, cursor, limit) -> BuildLogsResult:
        app = get_owner_authorized_app(user_id, app_id)
        build_id, task_id, finished = self._resolve_build(app, deployment_id)

        tokens: dict = {}
        if cursor:
            payload = decode_cursor(cursor, app_id=app.id, user_id=user_id, build_id=build_id)
            tokens = payload["t"]

        if task_id.startswith(platform_builder.MOCK_TASK_ID_PREFIX):
            return self._mock_result(app, build_id)

        per_stream_limit = _clamp_limit(limit)
        deadline = time.monotonic() + CALL_DEADLINE_SECONDS
        session = platform_builder.launcher_session_for_read(config=_budget_config(deadline))
        logs_client = session.client("logs", config=_budget_config(deadline))
        log_group = platform_builder.build_log_group()

        raw_events = []
        next_tokens = {}
        caught_up = True
        for container in CONTAINERS:
            stream_name = f"{platform_builder.LOG_STREAM_PREFIX}/{container}/{task_id}"
            events, forward_token, stream_caught_up = self._fetch_stream(
                logs_client, log_group, stream_name, tokens.get(container), per_stream_limit,
            )
            next_tokens[container] = forward_token
            caught_up = caught_up and stream_caught_up
            for event in events:
                raw_events.append((event.get("timestamp", 0), container, event.get("message", "")))

        raw_events.sort(key=lambda e: e[0])
        events_out, total_bytes = _cap_and_sanitize(raw_events)
        complete = finished and caught_up

        next_cursor = None
        if not complete:
            next_cursor = encode_cursor(app_id=app.id, user_id=user_id, build_id=build_id, tokens=next_tokens)

        return BuildLogsResult(
            events=events_out, next_cursor=next_cursor, complete=complete, build_id=build_id,
            infra_id=app.infrastructure_id, event_count=len(events_out), total_bytes=total_bytes,
        )

    def _resolve_build(self, app, deployment_id):
        """Returns (build_id, build_task_id, finished) — `finished` is True only for the
        deployment_id path, whose row is written (`_record_deployment`) strictly after
        `wait_for_build` returns, so the build it names has always already stopped. The
        in-progress path never claims `finished`: there is no cheap, reliable way to tell
        "caught up with the stream so far" apart from "the task has actually stopped"
        without an extra `DescribeTasks` call on every poll, so it simply keeps polling
        until the status check below 404s — at which point the deployment_id path takes
        over and reports the real, complete tail."""
        if deployment_id is not None:
            from api.models.deployment import Deployment

            try:
                deployment = Deployment.objects.get(id=deployment_id, application=app)
            except Deployment.DoesNotExist:
                raise NoBuildError("Deployment not found") from None
            if not deployment.build_id:
                raise NoBuildError("This deployment has no associated build")
            if not deployment.build_task_id:
                raise LegacyBuildError("This deployment used the legacy CodeBuild path")
            return deployment.build_id, deployment.build_task_id, True

        if not app.build_id:
            raise NoBuildError("No build associated with this application")
        if not app.build_task_id:
            raise LegacyBuildError("The current build used the legacy CodeBuild path")
        if app.status not in IN_PROGRESS_STATUSES:
            raise NoBuildError("No in-progress build")
        return app.build_id, app.build_task_id, False

    def _fetch_stream(self, logs_client, log_group, stream_name, token, limit):
        kwargs = {
            "logGroupName": log_group, "logStreamName": stream_name,
            "startFromHead": True, "limit": limit,
        }
        if token:
            kwargs["nextToken"] = token
        try:
            response = logs_client.get_log_events(**kwargs)
        except logs_client.exceptions.ResourceNotFoundException:
            # The stream doesn't exist yet (task still provisioning) or has already
            # rolled off the build log group's short retention — either way, "no events
            # from this stream right now" rather than an error.
            return [], token, True
        except (ConnectTimeoutError, ReadTimeoutError) as e:
            raise UpstreamError("Timed out contacting the build log stream", code="Timeout") from e
        except ClientError as e:
            raise UpstreamError("Upstream call failed", code=_aws_error_code(e)) from e

        events = response.get("events", [])
        forward_token = response.get("nextForwardToken") or token
        # Heuristic, not a guarantee: a page shorter than what we asked for means we've
        # caught up to the stream's current end. CloudWatch's own "same token twice in a
        # row" signal would be a stronger guarantee but costs a second round trip per
        # poll; a short-lived build log (minutes, not hours) makes this good enough.
        stream_caught_up = len(events) < limit
        return events, forward_token, stream_caught_up

    def _mock_result(self, app, build_id) -> BuildLogsResult:
        timestamp = datetime.now(timezone.utc).isoformat()
        return BuildLogsResult(
            events=[{
                "timestamp": timestamp, "container": platform_builder.KANIKO_CONTAINER_NAME,
                "message": "Mock build (dev mode) — no platform build logs are produced.",
            }],
            next_cursor=None, complete=True, build_id=build_id,
            infra_id=app.infrastructure_id, event_count=1, total_bytes=0,
        )


_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9.]{1,64}")


def _aws_error_code(exc: ClientError) -> str:
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    return code if _ERROR_CODE_RE.fullmatch(code) else "Unknown"


def _clamp_limit(limit) -> int:
    if limit is None:
        return DEFAULT_EVENTS_PER_STREAM
    return max(1, min(MAX_EVENTS_PER_STREAM, int(limit)))


# ── rendering safety + credential denylist ──────────────────────────────────────────
#
# ANSI cursor/color escapes, then C0/C1 control chars (tab and newline excepted) and the
# Unicode bidi embedding/override/isolate characters used in "Trojan Source"-style
# spoofing — identical in intent to runtime_logs_service.py's _sanitize_message. Built
# from codepoints rather than literal characters so this file never contains an
# invisible/control character itself.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_UNSAFE_CODEPOINTS = (
    list(range(0x09)) + list(range(0x0B, 0x20)) + [0x7F]  # C0 controls + DEL
    + list(range(0x80, 0xA0))  # C1 controls
    + list(range(0x202A, 0x202F))
    + list(range(0x2066, 0x206A))
)
_UNSAFE_CHAR_RE = re.compile("[" + "".join(chr(c) for c in _UNSAFE_CODEPOINTS) + "]")

# Credential shapes that would matter if ever echoed into build output: AWS access keys,
# a Basic/Bearer Authorization header, a raw docker-config "auth" value, and common
# personal-access-token prefixes (GitHub). Not a claim this is exhaustive — see this
# module's docstring — it is a denylist by design, not an allowlist.
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(r"(?i)\bAuthorization:\s*(?:Basic|Bearer)\s+\S+"),
    re.compile(r'"auth"\s*:\s*"[A-Za-z0-9+/=]+"'),
    # Security pre-review on #135: the same scoped ECR push credential this module's
    # docstring already worries about can also surface in these two shapes — an
    # environment-style assignment (`RUN export AWS_SECRET_ACCESS_KEY=...` echoed by a
    # careless build script) and the other docker-config fields alongside "auth" that
    # can carry a bare credential.
    re.compile(r"(?i)\baws_secret_access_key\s*[=:]\s*\S+"),
    re.compile(r"(?i)\baws_session_token\s*[=:]\s*\S+"),
    re.compile(r'"(?:password|identitytoken|registrytoken)"\s*:\s*"[^"]+"'),
    re.compile(r"\bgh[ps]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)x-access-token:[^@\s]+@"),
)


def _sanitize_message(message: str) -> str:
    message = _ANSI_ESCAPE_RE.sub("", message)
    message = _UNSAFE_CHAR_RE.sub("�", message)
    for pattern in _CREDENTIAL_PATTERNS:
        message = pattern.sub("<redacted>", message)
    return message


def _truncate_message(message: str) -> str:
    message = _sanitize_message(message)
    raw = message.encode("utf-8", "replace")
    if len(raw) <= MAX_MESSAGE_BYTES:
        return message
    return raw[:MAX_MESSAGE_BYTES].decode("utf-8", "ignore") + TRUNCATION_MARKER


def _cap_and_sanitize(raw_events) -> tuple:
    events = []
    total_bytes = 0
    for timestamp_ms, container, message in raw_events:
        if total_bytes >= MAX_TOTAL_BYTES:
            break
        message = _truncate_message(message)
        total_bytes += len(message.encode("utf-8", "replace"))
        events.append({
            "timestamp": datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(),
            "message": message,
            "container": container,
        })
    return events, total_bytes
