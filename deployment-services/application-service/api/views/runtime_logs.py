"""F2 runtime log tailing, plus its SSE counterpart (`runtime_logs_stream`): a long-lived
`text/event-stream` connection that tails the same source instead of being polled.

Concurrency note: application-service runs under `manage.py runserver`, a threaded WSGI
server, not an ASGI/async-capable one. Every open stream pins one of that server's worker
threads for up to `settings.LOG_STREAM_MAX_DURATION_SECONDS` (10 minutes by default), so
`settings.LOG_STREAM_MAX_CONCURRENT_PER_USER` is kept small (3). Production deployments of
this service should move to an ASGI server before raising either number; this change does
not touch the run command.
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

from api.services.runtime_logs_audit import record_access
from api.services.runtime_logs_cursor import InvalidCursor
from api.services.runtime_logs_service import (
    CONTAINERS,
    NotDeployedError,
    RuntimeLogsService,
    UpstreamError,
    UpstreamUnavailableError,
)
from api.services.runtime_logs_stream_cursor import (
    InvalidCursor as InvalidWatermark,
)
from api.services.runtime_logs_stream_cursor import (
    decode_watermark,
    encode_watermark,
)

logger = logging.getLogger(__name__)

runtime_logs_service = RuntimeLogsService()

RATE_LIMIT_BUCKET = "runtime_logs"
# Shared with build_logs.py's two stream views: one per-user budget for every kind of
# open log stream, not one budget per endpoint. A user holding three runtime-log tabs
# open must not also get a build-log stream for free.
STREAM_CONCURRENCY_KEY_PREFIX = "log_streams"
# Backstop TTL margin on top of the hard stream duration: covers the gap between a
# worker crashing mid-stream (skipping the generator's `finally`) and Redis expiring the
# key on its own.
STREAM_CONCURRENCY_TTL_MARGIN_SECONDS = 30


class RuntimeLogsQuerySerializer(serializers.Serializer):
    container = serializers.ChoiceField(choices=list(CONTAINERS), required=False, default="app")
    minutes = serializers.IntegerField(required=False, min_value=1, max_value=60)
    cursor = serializers.CharField(required=False, allow_blank=False, max_length=4096)
    previous = serializers.BooleanField(required=False, default=False)


class RuntimeLogEventSerializer(serializers.Serializer):
    timestamp = serializers.CharField()
    message = serializers.CharField()


class RuntimeLogsResponseSerializer(serializers.Serializer):
    events = RuntimeLogEventSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    truncated = serializers.BooleanField()


class RuntimeLogsErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


class RuntimeLogsStreamQuerySerializer(serializers.Serializer):
    container = serializers.ChoiceField(choices=list(CONTAINERS), required=False, default="app")


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


@extend_schema(
    summary="Tail an application's runtime logs",
    description=(
        "Owner only. Reads on demand from the customer's own CloudWatch Logs (ECS) or "
        "cluster (EKS) via the assumed deployment role — nothing is stored on the platform. "
        "Bounded window (max 60 minutes), bounded page size, budgeted per user."
    ),
    parameters=[
        OpenApiParameter("app_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        OpenApiParameter("container", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("minutes", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("cursor", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("previous", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False),
    ],
    responses={
        200: RuntimeLogsResponseSerializer,
        400: RuntimeLogsErrorSerializer,
        403: RuntimeLogsErrorSerializer,
        404: RuntimeLogsErrorSerializer,
        409: RuntimeLogsErrorSerializer,
        429: RuntimeLogsErrorSerializer,
        502: RuntimeLogsErrorSerializer,
        503: RuntimeLogsErrorSerializer,
    },
    methods=["GET"],
)
@csrf_exempt
@api_view(["GET"])
def runtime_logs(request: HttpRequest, app_id):
    request_id = str(uuid.uuid4())
    client_ip = _client_ip(request)
    started = time.monotonic()

    def _respond(response_status, body, *, headers=None, container=None, infra_id=None,
                 compute_type=None, window_start_ms=None, window_end_ms=None, cursor_present=False,
                 aws_error_code=None, event_count=0, total_bytes=0):
        record_access(
            request_id=request_id, user_id=request.user.id, app_id=app_id, infra_id=infra_id,
            compute_type=compute_type, container=container, window_start_ms=window_start_ms,
            window_end_ms=window_end_ms, cursor_present=cursor_present, status_code=response_status,
            aws_error_code=aws_error_code, event_count=event_count, total_bytes=total_bytes,
            duration_ms=int((time.monotonic() - started) * 1000), client_ip=client_ip,
        )
        return _no_store(Response(body, status=response_status, headers=headers), request_id)

    # Budgeted before params are even parsed: an unauthenticated-but-JWT-holding client
    # spamming malformed query strings would otherwise get unlimited free 400s — each one
    # still writes an audit row, so that path alone could grow the audit table without
    # bound if it weren't rate-limited like every other outcome on this endpoint.
    try:
        retry_after = customer_call_budget(
            request.user.id, RATE_LIMIT_BUCKET,
            limit=settings.RATE_BUDGET_RUNTIME_LOGS_LIMIT,
            window=settings.RATE_BUDGET_RUNTIME_LOGS_WINDOW_SECONDS,
        )
    except HttpError as e:
        return _respond(e.status_code, {"error": e.message})
    if retry_after is not None:
        return _respond(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"error": "Too many requests for this operation, try again later"},
            headers={"Retry-After": str(retry_after)},
        )

    allowed_params = set(RuntimeLogsQuerySerializer().fields)
    unknown = set(request.query_params) - allowed_params
    if unknown:
        return _respond(status.HTTP_400_BAD_REQUEST, {"error": "Unknown query parameter"})

    query = RuntimeLogsQuerySerializer(data=request.query_params)
    if not query.is_valid():
        return _respond(status.HTTP_400_BAD_REQUEST, {"error": "Invalid query parameters"})
    params = query.validated_data
    container = params.get("container", "app")
    cursor = params.get("cursor")

    try:
        result = runtime_logs_service.get_logs(
            user_id=request.user.id, app_id=app_id, container=container,
            minutes=params.get("minutes"), cursor=cursor, previous=params.get("previous", False),
        )
    except LookupError:
        return _respond(status.HTTP_404_NOT_FOUND, {"error": "Application not found"},
                         container=container, cursor_present=bool(cursor))
    except PermissionError:
        return _respond(status.HTTP_403_FORBIDDEN, {"error": "Forbidden"},
                         container=container, cursor_present=bool(cursor))
    except (ValueError, InvalidCursor):
        return _respond(status.HTTP_400_BAD_REQUEST, {"error": "Invalid request"},
                         container=container, cursor_present=bool(cursor))
    except NotDeployedError:
        return _respond(status.HTTP_409_CONFLICT, {"error": "Application is not deployed"},
                         container=container, cursor_present=bool(cursor))
    except UpstreamUnavailableError:
        return _respond(status.HTTP_503_SERVICE_UNAVAILABLE, {"error": "Service temporarily unavailable"},
                         container=container, cursor_present=bool(cursor))
    except UpstreamError as e:
        logger.warning("runtime_logs upstream error", extra={"request_id": request_id, "aws_error_code": e.code})
        return _respond(
            status.HTTP_502_BAD_GATEWAY, {"error": "Upstream call failed", "code": e.code},
            container=container, cursor_present=bool(cursor), aws_error_code=e.code,
        )
    except Exception as e:
        # Only the exception's type name — never str(e) or exc_info — reaches this logger.
        # An unexpected failure here is on the same path that reads customer log content;
        # the message of an arbitrary exception could contain a fragment of it.
        logger.error("Unhandled runtime logs error: %s", type(e).__name__, extra={"request_id": request_id})
        return _respond(status.HTTP_500_INTERNAL_SERVER_ERROR, {"error": "Internal error"},
                         container=container, cursor_present=bool(cursor))

    return _respond(
        status.HTTP_200_OK,
        {"events": result.events, "next_cursor": result.next_cursor, "truncated": result.truncated},
        container=result.container, infra_id=result.infra_id, compute_type=result.compute_type,
        window_start_ms=result.window_start_ms, window_end_ms=result.window_end_ms,
        cursor_present=bool(cursor), event_count=result.event_count, total_bytes=result.total_bytes,
    )


def _stream_generator(ctx, *, container, app_id, user_id, request_id, slot_key, request_started, watermark):
    """The tick loop. Everything before this generator starts running has already
    authorized and charged the request, so a failure in here can no longer change the
    response status: each failure mode emits `event: error` then `event: end` and
    returns, leaving reconnect/backoff (client-side) to re-charge the budget on retry
    rather than this one connection looping error frames for up to 10 minutes.

    `finally` always runs on exhaustion, an error return, or the generator being closed
    (Django's StreamingHttpResponse.close() calls `.close()` on this, which raises
    GeneratorExit at the currently suspended `yield`, the same path a client disconnect
    takes once a write to the closed socket fails), so the concurrency slot and (on EKS)
    the held-open k8s session are released exactly once no matter how the stream ends.
    """
    tick_seconds = settings.LOG_STREAM_TICK_SECONDS
    heartbeat_seconds = settings.LOG_STREAM_HEARTBEAT_SECONDS
    max_duration = settings.LOG_STREAM_MAX_DURATION_SECONDS
    stream_started = time.monotonic()
    last_frame_at = stream_started
    event_count = 0
    total_bytes = 0
    try:
        while True:
            if time.monotonic() - stream_started > max_duration:
                yield sse_event({"reason": "max_duration"}, event_type="end")
                return

            try:
                events, watermark = runtime_logs_service.tail_stream(ctx, watermark)
            except UpstreamError as e:
                logger.warning(
                    "runtime_logs stream upstream error",
                    extra={"request_id": request_id, "aws_error_code": e.code},
                )
                yield sse_event({"error": "Upstream call failed", "code": e.code}, event_type="error")
                yield sse_event({"reason": "upstream_error"}, event_type="end")
                return
            except Exception as e:
                # Only the exception's type name, never str(e) or exc_info, reaches
                # this logger, same posture as the JSON view: this is on the path that
                # reads customer log content.
                logger.error(
                    "Unhandled runtime logs stream error: %s", type(e).__name__,
                    extra={"request_id": request_id},
                )
                yield sse_event({"error": "Internal error"}, event_type="error")
                yield sse_event({"reason": "internal_error"}, event_type="end")
                return

            frame_id = encode_watermark(
                app_id=app_id, user_id=user_id, container=container,
                ts_ms=watermark["ts"], ids=watermark["ids"],
            )
            if events:
                event_count += len(events)
                total_bytes += sum(len(e.get("message", "").encode("utf-8", "replace")) for e in events)
                yield sse_event({"events": events}, event_type="message", frame_id=frame_id)
                last_frame_at = time.monotonic()

            now = time.monotonic()
            if now - last_frame_at >= heartbeat_seconds:
                # Carries the watermark's current id, not a bare `: ping` comment (which
                # has no `id:` field to carry): on a quiet stream, `events` above can go
                # untriggered for the whole heartbeat_seconds interval or longer, and a
                # reconnect's `Last-Event-ID` must not resume from a `message` frame that
                # is now minutes stale, re-replaying everything in between.
                yield sse_event({}, event_type="heartbeat", frame_id=frame_id)
                last_frame_at = now
            time.sleep(tick_seconds)
    finally:
        ctx.close()
        release_concurrency_slot(slot_key)
        logger.info(
            "runtime_logs stream closed",
            extra={
                "request_id": request_id, "event_count": event_count, "bytes": total_bytes,
                "duration_ms": int((time.monotonic() - request_started) * 1000),
            },
        )


@extend_schema(
    summary="Stream an application's runtime logs over SSE",
    description=(
        "Owner only. Same authorization, redaction, and no-store posture as GET .../logs, "
        "but a long-lived `text/event-stream` connection instead of a polled JSON "
        "response. The per-user rate budget and concurrent-stream cap are both charged "
        "once, on open; every subsequent tick is free. Resumable via `Last-Event-ID`. "
        "Ends with `event: end` after a hard 10-minute duration or an upstream error; "
        "the client is expected to reconnect with backoff."
    ),
    parameters=[
        OpenApiParameter("app_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        OpenApiParameter("container", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
    ],
    responses={
        400: RuntimeLogsErrorSerializer,
        403: RuntimeLogsErrorSerializer,
        404: RuntimeLogsErrorSerializer,
        409: RuntimeLogsErrorSerializer,
        429: RuntimeLogsErrorSerializer,
        502: RuntimeLogsErrorSerializer,
        503: RuntimeLogsErrorSerializer,
    },
    methods=["GET"],
)
@csrf_exempt
@api_view(["GET"])
def runtime_logs_stream(request: HttpRequest, app_id):
    request_id = str(uuid.uuid4())
    client_ip = _client_ip(request)
    started = time.monotonic()
    user_id = request.user.id

    def _error(response_status, body, *, headers=None, container=None):
        record_access(
            request_id=request_id, user_id=user_id, app_id=app_id, container=container,
            status_code=response_status, duration_ms=int((time.monotonic() - started) * 1000),
            client_ip=client_ip,
        )
        return _no_store(Response(body, status=response_status, headers=headers), request_id)

    try:
        retry_after = customer_call_budget(
            user_id, RATE_LIMIT_BUCKET,
            limit=settings.RATE_BUDGET_RUNTIME_LOGS_LIMIT,
            window=settings.RATE_BUDGET_RUNTIME_LOGS_WINDOW_SECONDS,
        )
    except HttpError as e:
        return _error(e.status_code, {"error": e.message})
    if retry_after is not None:
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"error": "Too many requests for this operation, try again later"},
            headers={"Retry-After": str(retry_after)},
        )

    allowed_params = set(RuntimeLogsStreamQuerySerializer().fields)
    unknown = set(request.query_params) - allowed_params
    if unknown:
        return _error(status.HTTP_400_BAD_REQUEST, {"error": "Unknown query parameter"})

    query = RuntimeLogsStreamQuerySerializer(data=request.query_params)
    if not query.is_valid():
        return _error(status.HTTP_400_BAD_REQUEST, {"error": "Invalid query parameters"})
    container = query.validated_data.get("container", "app")

    slot_key = f"{STREAM_CONCURRENCY_KEY_PREFIX}:{user_id}"
    slot_ttl = settings.LOG_STREAM_MAX_DURATION_SECONDS + STREAM_CONCURRENCY_TTL_MARGIN_SECONDS
    try:
        acquired = acquire_concurrency_slot(
            slot_key, limit=settings.LOG_STREAM_MAX_CONCURRENT_PER_USER, ttl=slot_ttl,
        )
    except HttpError as e:
        return _error(e.status_code, {"error": e.message}, container=container)
    if not acquired:
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"error": "Too many concurrent log streams open for this user"},
            container=container,
        )

    try:
        ctx = runtime_logs_service.open_stream(user_id=user_id, app_id=app_id, container=container)
    except LookupError:
        release_concurrency_slot(slot_key)
        return _error(status.HTTP_404_NOT_FOUND, {"error": "Application not found"}, container=container)
    except PermissionError:
        release_concurrency_slot(slot_key)
        return _error(status.HTTP_403_FORBIDDEN, {"error": "Forbidden"}, container=container)
    except ValueError:
        release_concurrency_slot(slot_key)
        return _error(status.HTTP_400_BAD_REQUEST, {"error": "Invalid request"}, container=container)
    except NotDeployedError:
        release_concurrency_slot(slot_key)
        return _error(status.HTTP_409_CONFLICT, {"error": "Application is not deployed"}, container=container)
    except UpstreamUnavailableError:
        release_concurrency_slot(slot_key)
        return _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, {"error": "Service temporarily unavailable"},
            container=container,
        )
    except UpstreamError as e:
        release_concurrency_slot(slot_key)
        logger.warning(
            "runtime_logs stream open upstream error",
            extra={"request_id": request_id, "aws_error_code": e.code},
        )
        return _error(
            status.HTTP_502_BAD_GATEWAY, {"error": "Upstream call failed", "code": e.code},
            container=container,
        )
    except Exception as e:
        release_concurrency_slot(slot_key)
        logger.error(
            "Unhandled runtime logs stream open error: %s", type(e).__name__,
            extra={"request_id": request_id},
        )
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, {"error": "Internal error"}, container=container)

    watermark = None
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id:
        try:
            watermark = decode_watermark(last_event_id, app_id=app_id, user_id=user_id, container=container)
        except InvalidWatermark:
            # Best-effort resume: an expired/tampered/foreign cursor just falls back to
            # a fresh live tail from "now" rather than failing the reconnect outright.
            watermark = None

    record_access(
        request_id=request_id, user_id=user_id, app_id=app_id, infra_id=ctx.infra.id,
        compute_type=ctx.compute_type, container=container, status_code=status.HTTP_200_OK,
        duration_ms=int((time.monotonic() - started) * 1000), client_ip=client_ip,
    )

    response = StreamingHttpResponse(
        _stream_generator(
            ctx, container=container, app_id=app_id, user_id=user_id, request_id=request_id,
            slot_key=slot_key, request_started=started, watermark=watermark,
        ),
        content_type=SSE_CONTENT_TYPE,
    )
    response["X-Request-Id"] = request_id
    for header, value in SSE_HEADERS.items():
        response[header] = value
    return response
