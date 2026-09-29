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

from api.services.app_metrics_service import (
    RANGE_SPECS,
    AppMetricsService,
    MetricsUnavailableError,
)
from api.services.owner_only import NotDeployedError
from api.services.policy_errors import PolicyRefreshRequiredError

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("audit.app_metrics")

app_metrics_service = AppMetricsService()

RATE_LIMIT_BUCKET = "app_metrics"


class AppMetricsQuerySerializer(serializers.Serializer):
    range = serializers.ChoiceField(choices=list(RANGE_SPECS), required=False, default="1h")


class MetricPointSerializer(serializers.Serializer):
    t = serializers.CharField()
    v = serializers.FloatField()


class AppMetricsSeriesSerializer(serializers.Serializer):
    cpu_percent = MetricPointSerializer(many=True)
    memory_percent = MetricPointSerializer(many=True)
    request_count = MetricPointSerializer(many=True)
    latency_p50_ms = MetricPointSerializer(many=True)
    latency_p95_ms = MetricPointSerializer(many=True)
    http_4xx = MetricPointSerializer(many=True)
    http_5xx = MetricPointSerializer(many=True)
    healthy_targets = MetricPointSerializer(many=True)


class AppMetricsResponseSerializer(serializers.Serializer):
    range = serializers.CharField()
    period_seconds = serializers.IntegerField()
    compute_type = serializers.CharField()
    generated_at = serializers.CharField()
    cached = serializers.BooleanField()
    series = AppMetricsSeriesSerializer()
    unavailable = serializers.DictField(child=serializers.CharField())


class AppMetricsErrorSerializer(serializers.Serializer):
    error = serializers.CharField()
    code = serializers.CharField()


def _client_ip(request: HttpRequest) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _no_store(response: Response) -> Response:
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@extend_schema(
    summary="Per-application metrics for dashboard charts",
    description=(
        "Owner only. Reads on demand from the customer's own CloudWatch via the assumed "
        "deployment role, cached for 30s per (app, range). `unavailable` maps a series key "
        "to a machine reason when that series can't be produced for this app's compute "
        "type or current AWS permissions."
    ),
    parameters=[
        OpenApiParameter("app_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        OpenApiParameter("range", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
                          description="One of 1h|6h|24h|7d, default 1h"),
    ],
    responses={
        200: AppMetricsResponseSerializer,
        400: AppMetricsErrorSerializer,
        403: AppMetricsErrorSerializer,
        404: AppMetricsErrorSerializer,
        409: AppMetricsErrorSerializer,
        422: AppMetricsErrorSerializer,
        429: AppMetricsErrorSerializer,
        503: AppMetricsErrorSerializer,
    },
    methods=["GET"],
)
@csrf_exempt
@api_view(["GET"])
def application_metrics(request: HttpRequest, app_id):
    request_id = str(uuid.uuid4())
    client_ip = _client_ip(request)
    started = time.monotonic()

    def _respond(response_status, body):
        duration_ms = int((time.monotonic() - started) * 1000)
        audit_logger.info(
            "app_metrics access",
            extra={
                "request_id": request_id, "user_id": request.user.id,
                "app_id": str(app_id), "status_code": response_status,
                "duration_ms": duration_ms, "client_ip": client_ip,
            },
        )
        response = Response(body, status=response_status)
        response["X-Request-Id"] = request_id
        return _no_store(response)

    # Budgeted before params are parsed — this endpoint costs an AssumeRole plus a
    # CloudWatch call in the customer's own account on every cache miss, same rationale
    # as runtime_logs.py.
    try:
        retry_after = customer_call_budget(
            request.user.id, RATE_LIMIT_BUCKET,
            limit=settings.RATE_BUDGET_APP_METRICS_LIMIT,
            window=settings.RATE_BUDGET_APP_METRICS_WINDOW_SECONDS,
        )
    except HttpError as e:
        return _respond(e.status_code, {"error": e.message, "code": "rate_limiter_unavailable"})
    if retry_after is not None:
        response = _respond(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"error": "Too many requests for this operation, try again later", "code": "rate_limited"},
        )
        response["Retry-After"] = str(retry_after)
        return response

    allowed_params = set(AppMetricsQuerySerializer().fields)
    unknown = set(request.query_params) - allowed_params
    if unknown:
        return _respond(status.HTTP_400_BAD_REQUEST, {"error": "Unknown query parameter", "code": "bad_request"})

    query = AppMetricsQuerySerializer(data=request.query_params)
    if not query.is_valid():
        return _respond(status.HTTP_400_BAD_REQUEST, {"error": "Invalid query parameters", "code": "invalid_range"})
    time_range = query.validated_data["range"]

    try:
        result = app_metrics_service.get_metrics(user_id=request.user.id, app_id=app_id, time_range=time_range)
    except LookupError:
        return _respond(status.HTTP_404_NOT_FOUND, {"error": "Application not found", "code": "not_found"})
    except PermissionError:
        return _respond(status.HTTP_403_FORBIDDEN, {"error": "Forbidden", "code": "forbidden"})
    except NotDeployedError:
        return _respond(status.HTTP_409_CONFLICT, {"error": "Application is not deployed", "code": "not_deployed"})
    except PolicyRefreshRequiredError as e:
        return _respond(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "error": str(e), "code": "policy_refresh_required", "denied_actions": e.denied_actions,
        })
    except MetricsUnavailableError:
        return _respond(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "error": "Metrics are temporarily unavailable, try again shortly", "code": "metrics_unavailable",
        })
    except ValueError:
        return _respond(status.HTTP_400_BAD_REQUEST, {"error": "Invalid request", "code": "bad_request"})
    except Exception as e:
        logger.error("Unhandled app_metrics error: %s", type(e).__name__, extra={"request_id": request_id})
        return _respond(status.HTTP_500_INTERNAL_SERVER_ERROR, {"error": "Internal error", "code": "internal_error"})

    return _respond(status.HTTP_200_OK, result.to_dict())
