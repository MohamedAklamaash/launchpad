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
"""
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

from api.services.build_logs_audit import record_access
from api.services.build_logs_cursor import InvalidCursor
from api.services.build_logs_service import (
    BuildLogsService,
    LegacyBuildError,
    NoBuildError,
    UpstreamError,
)

logger = logging.getLogger(__name__)

build_logs_service = BuildLogsService()

RATE_LIMIT_BUCKET = "build_logs"


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
