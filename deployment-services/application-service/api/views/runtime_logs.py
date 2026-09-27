import logging
import time
import uuid

from django.conf import settings
from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from shared.errors.exception import HttpError
from shared.ratelimit.budget import customer_call_budget

from api.services.runtime_logs_audit import record_access
from api.services.runtime_logs_cursor import InvalidCursor
from api.services.runtime_logs_service import (
    CONTAINERS,
    NotDeployedError,
    RuntimeLogsService,
    UpstreamError,
    UpstreamUnavailableError,
)

logger = logging.getLogger(__name__)

runtime_logs_service = RuntimeLogsService()

RATE_LIMIT_BUCKET = "runtime_logs"


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
