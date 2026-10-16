"""F7 ticket 8: build-log tail for platform builds. Follows `runtime_logs.py`'s (F2)
endpoint shape exactly — owner-only, a dedicated `customer_call_budget` bucket, signed
cursor, no-store headers, and a metadata-only audit row for every outcome.

Two routes share one service (`BuildLogsService`) and query/response shape:
- `GET /applications/<app_id>/deployments/<deployment_id>/build-logs` — a completed
  deploy attempt's build, read from its `Deployment` row.
- `GET /applications/<app_id>/build-logs/current` — the in-progress build, read off
  `Application.build_id`/`build_task_id`, valid only while `Application.status` is
  BUILDING or PUSHING_IMAGE. Not in the original ticket text, but required for the
  frontend's "live-tails while BUILDING/PUSHING_IMAGE" requirement: `Deployment` is
  append-only and its row for this attempt does not exist until the build finishes
  (`_record_deployment` runs after `wait_for_build` returns), so the deployment_id route
  alone cannot serve a live tail. See the PR description for this reconciliation.

Both routes also have an SSE counterpart (`build_logs_stream`, `current_build_logs_stream`):
a long-lived `text/event-stream` connection that tails the same source instead of being
polled. Concurrency note: application-service runs under `manage.py runserver`, a
threaded WSGI server, not an ASGI/async-capable one. Every open stream pins one of that
server's worker threads for up to `settings.LOG_STREAM_MAX_DURATION_SECONDS` (10 minutes
by default), so `settings.LOG_STREAM_MAX_CONCURRENT_PER_USER` is kept small (3) and
shared with runtime_logs.py's stream. Production deployments of this service should move
to an ASGI server before raising either number; this change does not touch the run
command.
"""
import logging
import time
import uuid

from django.conf import settings
from django.http import HttpRequest, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from shared.errors.exception import HttpError
from shared.ratelimit.budget import (
    acquire_concurrency_slot,
    customer_call_budget,
    release_concurrency_slot,
)
from shared.sse.framing import SSE_CONTENT_TYPE, SSE_HEADERS
from shared.sse.framing import event as sse_event

from api.services.build_logs_audit import record_access
from api.services.build_logs_cursor import InvalidCursor, decode_cursor, encode_cursor
from api.services.build_logs_service import (
    IN_PROGRESS_STATUSES,
    BuildLogsService,
    LegacyBuildError,
    NoBuildError,
    UpstreamError,
)

logger = logging.getLogger(__name__)

build_logs_service = BuildLogsService()

RATE_LIMIT_BUCKET = "build_logs"
# Shared with runtime_logs.py's stream view: see that module's constant for why one
# budget covers every kind of open log stream rather than one per endpoint.
STREAM_CONCURRENCY_KEY_PREFIX = "log_streams"
STREAM_CONCURRENCY_TTL_MARGIN_SECONDS = 30


class BuildLogsQuerySerializer(serializers.Serializer):
    cursor = serializers.CharField(required=False, allow_blank=False, max_length=4096)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=500)


class BuildLogEventSerializer(serializers.Serializer):
    timestamp = serializers.CharField()
    message = serializers.CharField()
    container = serializers.CharField()


class BuildLogsResponseSerializer(serializers.Serializer):
    events = BuildLogEventSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    complete = serializers.BooleanField()


class BuildLogsErrorSerializer(serializers.Serializer):
    error = serializers.CharField()
    code = serializers.CharField(required=False)


def _client_ip(request: HttpRequest) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _no_store(response: Response, request_id: str) -> Response:
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Request-Id"] = request_id
    return response


def _handle(request: HttpRequest, app_id, deployment_id):
    request_id = str(uuid.uuid4())
    client_ip = _client_ip(request)
    started = time.monotonic()

    def _respond(response_status, body, *, headers=None, infra_id=None, cursor_present=False,
                 aws_error_code=None, event_count=0, total_bytes=0):
        record_access(
            request_id=request_id, user_id=request.user.id, app_id=app_id,
            deployment_id=deployment_id, infra_id=infra_id, cursor_present=cursor_present,
            status_code=response_status, aws_error_code=aws_error_code, event_count=event_count,
            total_bytes=total_bytes, duration_ms=int((time.monotonic() - started) * 1000),
            client_ip=client_ip,
        )
        return _no_store(Response(body, status=response_status, headers=headers), request_id)

    try:
        retry_after = customer_call_budget(
            request.user.id, RATE_LIMIT_BUCKET,
            limit=settings.RATE_BUDGET_BUILD_LOGS_LIMIT,
            window=settings.RATE_BUDGET_BUILD_LOGS_WINDOW_SECONDS,
        )
    except HttpError as e:
        return _respond(e.status_code, {"error": e.message})
    if retry_after is not None:
        return _respond(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"error": "Too many requests for this operation, try again later"},
            headers={"Retry-After": str(retry_after)},
        )

    allowed_params = set(BuildLogsQuerySerializer().fields)
    unknown = set(request.query_params) - allowed_params
    if unknown:
        return _respond(status.HTTP_400_BAD_REQUEST, {"error": "Unknown query parameter"})

    query = BuildLogsQuerySerializer(data=request.query_params)
    if not query.is_valid():
        return _respond(status.HTTP_400_BAD_REQUEST, {"error": "Invalid query parameters"})
    params = query.validated_data
    cursor = params.get("cursor")

    try:
        result = build_logs_service.get_logs(
            user_id=request.user.id, app_id=app_id, deployment_id=deployment_id,
            cursor=cursor, limit=params.get("limit"),
        )
    except LegacyBuildError as e:
        return _respond(status.HTTP_409_CONFLICT, {"error": str(e), "code": e.code}, cursor_present=bool(cursor))
    except NoBuildError as e:
        return _respond(status.HTTP_404_NOT_FOUND, {"error": str(e)}, cursor_present=bool(cursor))
    except PermissionError as e:
        return _respond(status.HTTP_403_FORBIDDEN, {"error": str(e)}, cursor_present=bool(cursor))
    except LookupError as e:
        return _respond(status.HTTP_404_NOT_FOUND, {"error": str(e)}, cursor_present=bool(cursor))
    except (ValueError, InvalidCursor):
        return _respond(status.HTTP_400_BAD_REQUEST, {"error": "Invalid request"}, cursor_present=bool(cursor))
    except UpstreamError as e:
        logger.warning("build_logs upstream error", extra={"request_id": request_id, "aws_error_code": e.code})
        return _respond(
            status.HTTP_502_BAD_GATEWAY, {"error": "Upstream call failed", "code": e.code},
            cursor_present=bool(cursor), aws_error_code=e.code,
        )
    except Exception as e:
        # Only the exception's type name — never str(e) or exc_info — reaches this logger.
        # An unexpected failure here is on the same path that reads build output; the
        # message of an arbitrary exception could contain a fragment of it.
        logger.error("Unhandled build logs error: %s", type(e).__name__, extra={"request_id": request_id})
        return _respond(status.HTTP_500_INTERNAL_SERVER_ERROR, {"error": "Internal error"}, cursor_present=bool(cursor))

    return _respond(
        status.HTTP_200_OK,
        {"events": result.events, "next_cursor": result.next_cursor, "complete": result.complete},
        infra_id=result.infra_id, cursor_present=bool(cursor),
        event_count=result.event_count, total_bytes=result.total_bytes,
    )


@extend_schema(
    summary="Tail a completed deployment's build logs",
    description=(
        "Owner only. Reads the platform build's init/kaniko CloudWatch streams via the "
        "builder-launcher role — nothing is stored on the platform beyond CloudWatch's own "
        "retention. 404 if this deployment has no associated build (a rollback, or a row "
        "predating this field); 409 code=legacy_build if it used the pre-F7 CodeBuild path."
    ),
    parameters=[
        OpenApiParameter("app_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        OpenApiParameter("deployment_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        OpenApiParameter("cursor", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
    ],
    responses={
        200: BuildLogsResponseSerializer,
        400: BuildLogsErrorSerializer,
        403: BuildLogsErrorSerializer,
        404: BuildLogsErrorSerializer,
        409: BuildLogsErrorSerializer,
        429: BuildLogsErrorSerializer,
        502: BuildLogsErrorSerializer,
        503: BuildLogsErrorSerializer,
    },
    methods=["GET"],
)
@csrf_exempt
@api_view(["GET"])
def build_logs(request: HttpRequest, app_id, deployment_id):
    return _handle(request, app_id, deployment_id)


@extend_schema(
    summary="Tail the in-progress build's logs",
    description=(
        "Owner only. Valid only while the application's status is BUILDING or "
        "PUSHING_IMAGE — 404 once the attempt finishes (poll the deployment-history "
        "build-logs route instead once the new Deployment row appears)."
    ),
    parameters=[
        OpenApiParameter("app_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        OpenApiParameter("cursor", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
    ],
    responses={
        200: BuildLogsResponseSerializer,
        400: BuildLogsErrorSerializer,
        403: BuildLogsErrorSerializer,
        404: BuildLogsErrorSerializer,
        409: BuildLogsErrorSerializer,
        429: BuildLogsErrorSerializer,
        502: BuildLogsErrorSerializer,
        503: BuildLogsErrorSerializer,
    },
    methods=["GET"],
)
@csrf_exempt
@api_view(["GET"])
def current_build_logs(request: HttpRequest, app_id):
    return _handle(request, app_id, None)


class BuildLogsStreamQuerySerializer(serializers.Serializer):
    """No query params of its own: resume uses the `Last-Event-ID` header, not a query
    string cursor. Still a serializer (rather than skipping validation outright) so
    an unrecognized query param 400s the same way every other endpoint here does."""


def _build_stream_generator(ctx, *, app_id, deployment_id, user_id, request_id, slot_key, request_started, tokens):
    """The tick loop. See runtime_logs.py's `_stream_generator` for the shared
    error/heartbeat/release posture, identical here. The one build-logs-specific piece:
    the in-progress path (`deployment_id is None`) re-checks `Application.status`/
    `build_id` on every tick rather than trusting `ctx.finished` (always False for that
    path at open time, by design, see `BuildStreamContext`'s docstring) and, once the
    build has moved on, does one more immediate tail to catch trailing output before
    ending: the task has already stopped, so nothing further will ever arrive."""
    tick_seconds = settings.LOG_STREAM_TICK_SECONDS
    heartbeat_seconds = settings.LOG_STREAM_HEARTBEAT_SECONDS
    max_duration = settings.LOG_STREAM_MAX_DURATION_SECONDS
    stream_started = time.monotonic()
    last_frame_at = stream_started
    event_count = 0
    total_bytes = 0

    def _frame_id(current_tokens):
        return encode_cursor(app_id=app_id, user_id=user_id, build_id=ctx.build_id, tokens=current_tokens)

    def _emit(events_batch, current_tokens):
        nonlocal event_count, total_bytes, last_frame_at
        if not events_batch:
            return
        event_count += len(events_batch)
        total_bytes += sum(len(e.get("message", "").encode("utf-8", "replace")) for e in events_batch)
        yield sse_event({"events": events_batch}, event_type="message", frame_id=_frame_id(current_tokens))
        last_frame_at = time.monotonic()

    try:
        if ctx.mock:
            yield from _emit([build_logs_service.mock_stream_event()], tokens)
            yield sse_event({"reason": "build_finished"}, event_type="end")
            return

        while True:
            if time.monotonic() - stream_started > max_duration:
                yield sse_event({"reason": "max_duration"}, event_type="end")
                return

            try:
                events, tokens, caught_up = build_logs_service.tail_stream(ctx, tokens)
            except UpstreamError as e:
                logger.warning(
                    "build_logs stream upstream error",
                    extra={"request_id": request_id, "aws_error_code": e.code},
                )
                yield sse_event({"error": "Upstream call failed", "code": e.code}, event_type="error")
                yield sse_event({"reason": "upstream_error"}, event_type="end")
                return
            except Exception as e:
                logger.error(
                    "Unhandled build logs stream error: %s", type(e).__name__,
                    extra={"request_id": request_id},
                )
                yield sse_event({"error": "Internal error"}, event_type="error")
                yield sse_event({"reason": "internal_error"}, event_type="end")
                return

            yield from _emit(events, tokens)

            build_finished = ctx.finished
            if not build_finished:
                ctx.app.refresh_from_db(fields=["status", "build_id"])
                build_finished = (
                    ctx.app.status not in IN_PROGRESS_STATUSES or str(ctx.app.build_id) != str(ctx.build_id)
                )

            if build_finished:
                if not caught_up:
                    try:
                        drain_events, tokens, _ = build_logs_service.tail_stream(ctx, tokens)
                        yield from _emit(drain_events, tokens)
                    except Exception as e:
                        # The build has already stopped, so a failure on this one extra
                        # drain attempt just means the stream ends without its very last
                        # few lines rather than the whole close failing.
                        logger.warning(
                            "build_logs stream final drain failed: %s", type(e).__name__,
                            extra={"request_id": request_id},
                        )
                yield sse_event({"reason": "build_finished"}, event_type="end")
                return

            now = time.monotonic()
            if now - last_frame_at >= heartbeat_seconds:
                # Carries the current cursor, not a bare `: ping` comment (which has no
                # `id:` field to carry): on a quiet build, a reconnect's `Last-Event-ID`
                # must not resume from a `message` frame that is now minutes stale.
                yield sse_event({}, event_type="heartbeat", frame_id=_frame_id(tokens))
                last_frame_at = now
            time.sleep(tick_seconds)
    finally:
        release_concurrency_slot(slot_key)
        logger.info(
            "build_logs stream closed",
            extra={
                "request_id": request_id, "event_count": event_count, "bytes": total_bytes,
                "duration_ms": int((time.monotonic() - request_started) * 1000),
            },
        )


def _handle_stream(request: HttpRequest, app_id, deployment_id):
    request_id = str(uuid.uuid4())
    client_ip = _client_ip(request)
    started = time.monotonic()
    user_id = request.user.id

    def _error(response_status, body, *, headers=None):
        record_access(
            request_id=request_id, user_id=user_id, app_id=app_id, deployment_id=deployment_id,
            status_code=response_status, duration_ms=int((time.monotonic() - started) * 1000),
            client_ip=client_ip,
        )
        return _no_store(Response(body, status=response_status, headers=headers), request_id)

    try:
        retry_after = customer_call_budget(
            user_id, RATE_LIMIT_BUCKET,
            limit=settings.RATE_BUDGET_BUILD_LOGS_LIMIT,
            window=settings.RATE_BUDGET_BUILD_LOGS_WINDOW_SECONDS,
        )
    except HttpError as e:
        return _error(e.status_code, {"error": e.message})
    if retry_after is not None:
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"error": "Too many requests for this operation, try again later"},
            headers={"Retry-After": str(retry_after)},
        )

    allowed_params = set(BuildLogsStreamQuerySerializer().fields)
    unknown = set(request.query_params) - allowed_params
    if unknown:
        return _error(status.HTTP_400_BAD_REQUEST, {"error": "Unknown query parameter"})

    slot_key = f"{STREAM_CONCURRENCY_KEY_PREFIX}:{user_id}"
    slot_ttl = settings.LOG_STREAM_MAX_DURATION_SECONDS + STREAM_CONCURRENCY_TTL_MARGIN_SECONDS
    try:
        acquired = acquire_concurrency_slot(
            slot_key, limit=settings.LOG_STREAM_MAX_CONCURRENT_PER_USER, ttl=slot_ttl,
        )
    except HttpError as e:
        return _error(e.status_code, {"error": e.message})
    if not acquired:
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"error": "Too many concurrent log streams open for this user"},
        )

    try:
        ctx = build_logs_service.open_stream(user_id=user_id, app_id=app_id, deployment_id=deployment_id)
    except LegacyBuildError as e:
        release_concurrency_slot(slot_key)
        return _error(status.HTTP_409_CONFLICT, {"error": str(e), "code": e.code})
    except NoBuildError as e:
        release_concurrency_slot(slot_key)
        return _error(status.HTTP_404_NOT_FOUND, {"error": str(e)})
    except PermissionError as e:
        release_concurrency_slot(slot_key)
        return _error(status.HTTP_403_FORBIDDEN, {"error": str(e)})
    except LookupError as e:
        release_concurrency_slot(slot_key)
        return _error(status.HTTP_404_NOT_FOUND, {"error": str(e)})
    except UpstreamError as e:
        release_concurrency_slot(slot_key)
        logger.warning(
            "build_logs stream open upstream error",
            extra={"request_id": request_id, "aws_error_code": e.code},
        )
        return _error(status.HTTP_502_BAD_GATEWAY, {"error": "Upstream call failed", "code": e.code})
    except Exception as e:
        release_concurrency_slot(slot_key)
        logger.error(
            "Unhandled build logs stream open error: %s", type(e).__name__,
            extra={"request_id": request_id},
        )
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, {"error": "Internal error"})

    tokens: dict = {}
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id:
        try:
            payload = decode_cursor(last_event_id, app_id=app_id, user_id=user_id, build_id=ctx.build_id)
            tokens = payload["t"]
        except InvalidCursor:
            # Best-effort resume: an expired/tampered/foreign cursor (or one minted for a
            # build this application has since moved past) just restarts from the
            # beginning of this build's streams rather than failing the reconnect.
            tokens = {}

    record_access(
        request_id=request_id, user_id=user_id, app_id=app_id, deployment_id=deployment_id,
        infra_id=ctx.app.infrastructure_id, status_code=status.HTTP_200_OK,
        duration_ms=int((time.monotonic() - started) * 1000), client_ip=client_ip,
    )

    response = StreamingHttpResponse(
        _build_stream_generator(
            ctx, app_id=app_id, deployment_id=deployment_id, user_id=user_id, request_id=request_id,
            slot_key=slot_key, request_started=started, tokens=tokens,
        ),
        content_type=SSE_CONTENT_TYPE,
    )
    response["X-Request-Id"] = request_id
    for header, value in SSE_HEADERS.items():
        response[header] = value
    return response


@extend_schema(
    summary="Stream a completed deployment's build logs over SSE",
    description=(
        "Owner only. Same authorization and redaction posture as GET .../build-logs, but "
        "a long-lived `text/event-stream` connection instead of a polled JSON response. "
        "Ends with `event: end` once the stream has caught up to the build's end, after "
        "a hard 10-minute duration, or on an upstream error."
    ),
    parameters=[
        OpenApiParameter("app_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        OpenApiParameter("deployment_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
    ],
    responses={
        403: BuildLogsErrorSerializer,
        404: BuildLogsErrorSerializer,
        409: BuildLogsErrorSerializer,
        429: BuildLogsErrorSerializer,
        502: BuildLogsErrorSerializer,
        503: BuildLogsErrorSerializer,
    },
    methods=["GET"],
)
@csrf_exempt
@api_view(["GET"])
def build_logs_stream(request: HttpRequest, app_id, deployment_id):
    return _handle_stream(request, app_id, deployment_id)


@extend_schema(
    summary="Stream the in-progress build's logs over SSE",
    description=(
        "Owner only. Ends with `event: end` the moment the application's status leaves "
        "BUILDING/PUSHING_IMAGE or moves on to a different build; switch to the "
        "deployment-history build-logs stream once that deploy's row appears."
    ),
    parameters=[
        OpenApiParameter("app_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
    ],
    responses={
        403: BuildLogsErrorSerializer,
        404: BuildLogsErrorSerializer,
        409: BuildLogsErrorSerializer,
        429: BuildLogsErrorSerializer,
        502: BuildLogsErrorSerializer,
        503: BuildLogsErrorSerializer,
    },
    methods=["GET"],
)
@csrf_exempt
@api_view(["GET"])
def current_build_logs_stream(request: HttpRequest, app_id):
    return _handle_stream(request, app_id, None)
