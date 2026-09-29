import logging

from api.services.cost_service import (
    CostAllocationTagsNotActivatedError,
    CostExplorerNotEnabledError,
    CostExplorerTemporarilyUnavailableError,
    CostService,
)
from api.services.policy_errors import PolicyRefreshRequiredError
from django.conf import settings
from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from shared.ratelimit import rate_limited

logger = logging.getLogger(__name__)

cost_service = CostService()


class AppCostSerializer(serializers.Serializer):
    app = serializers.CharField()
    amount_usd = serializers.FloatField()
    source = serializers.ChoiceField(choices=['actual', 'estimate', 'mock'])


class SharedCostSerializer(serializers.Serializer):
    amount_usd = serializers.FloatField()
    source = serializers.ChoiceField(choices=['actual', 'estimate', 'mock'])
    note = serializers.CharField(required=False)


class TagActivationSerializer(serializers.Serializer):
    activated = serializers.BooleanField(allow_null=True)
    reason = serializers.CharField(allow_null=True)


class CostsResponseSerializer(serializers.Serializer):
    infrastructure_id = serializers.CharField()
    compute_type = serializers.CharField()
    window_start = serializers.DateField()
    window_end = serializers.DateField()
    currency = serializers.CharField()
    apps = AppCostSerializer(many=True)
    shared = SharedCostSerializer()
    tag_activation = TagActivationSerializer()
    is_mock = serializers.BooleanField()
    cached = serializers.BooleanField()


class CostsErrorSerializer(serializers.Serializer):
    error = serializers.CharField()
    code = serializers.CharField(required=False)


def _error_response(e: Exception):
    if isinstance(e, PolicyRefreshRequiredError):
        return Response(
            {'error': str(e), 'code': 'policy_refresh_required', 'denied_actions': e.denied_actions},
            status=422,
        )
    if isinstance(e, CostExplorerNotEnabledError):
        return Response({'error': str(e), 'code': 'cost_explorer_not_enabled'}, status=422)
    if isinstance(e, CostAllocationTagsNotActivatedError):
        return Response({'error': str(e), 'code': 'cost_tags_not_activated'}, status=422)
    if isinstance(e, CostExplorerTemporarilyUnavailableError):
        return Response({'error': str(e), 'code': 'cost_explorer_unavailable'}, status=503)
    if isinstance(e, LookupError):
        return Response({'error': str(e)}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(e, PermissionError):
        return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(e, ValueError):
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    logger.error("Unhandled costs view error", exc_info=e)
    return Response({'error': 'Internal error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Per-app cost attribution for an infrastructure",
    description=(
        "Owner only — invited users get 403. ECS infrastructures return Cost Explorer actuals "
        "grouped by app (source=actual); EKS infrastructures return a labelled estimate from pod "
        "resource requests (source=estimate). `shared` covers spend that cannot be split per app "
        "(ALB, NAT gateway, EKS control plane, VPC). Returns 422 with policy_refresh_required if "
        "the customer's applied IAM policy predates the Cost Explorer grants (v3), 422 with "
        "cost_tags_not_activated if Cost Explorer rejects the tagged query outright (the tags "
        "likely aren't activated yet), or 503 with cost_explorer_unavailable on a transient "
        "Cost Explorer condition (rate limit, data not yet available)."
    ),
    parameters=[
        OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH),
        OpenApiParameter(
            "months", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False,
            description="Trailing window in months, 1-3 (default 1).",
        ),
    ],
    responses={
        200: CostsResponseSerializer,
        400: CostsErrorSerializer,
        403: CostsErrorSerializer,
        404: CostsErrorSerializer,
        422: CostsErrorSerializer,
        503: CostsErrorSerializer,
    },
    methods=["GET"],
)
@csrf_exempt
@api_view(['GET'])
@rate_limited('costs', limit=settings.RATE_BUDGET_COSTS_LIMIT, window=settings.RATE_BUDGET_COSTS_WINDOW_SECONDS)
def infrastructure_costs(request: HttpRequest, infra_id):
    try:
        months = int(request.GET.get('months', '1'))
    except ValueError:
        return Response({'error': 'months must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        payload = cost_service.get_costs(user_id=request.user.id, infra_id=infra_id, months=months)
        return Response(payload)
    except Exception as e:
        return _error_response(e)
