import logging

from api.services.provisioning_logs_service import ProvisioningLogsService
from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)

provisioning_logs_service = ProvisioningLogsService()


class ProvisioningLogsResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    error_message = serializers.CharField(allow_null=True)
    logs = serializers.CharField(help_text="Allowlist-redacted terraform output; newest at the tail")
    withheld_lines = serializers.IntegerField()
    truncated = serializers.BooleanField(help_text="True when the head was clipped to the storage cap")
    updated_at = serializers.DateTimeField()


class ProvisioningLogsErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


def _error_response(e: Exception):
    if isinstance(e, LookupError):
        return Response({'error': str(e)}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(e, PermissionError):
        return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(e, ValueError):
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    logger.error("Unhandled provisioning logs view error", exc_info=e)
    return Response({'error': 'Internal error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Provisioning logs for an infrastructure",
    description="Owner only — invited users get 403. Redacted terraform output, the last failure reason, and the environment status.",
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    responses={
        200: ProvisioningLogsResponseSerializer,
        403: ProvisioningLogsErrorSerializer,
        404: ProvisioningLogsErrorSerializer,
    },
    methods=["GET"],
)
@csrf_exempt
@api_view(['GET'])
def provisioning_logs(request: HttpRequest, infra_id):
    try:
        payload = provisioning_logs_service.get_logs(user_id=request.user.id, infra_id=infra_id)
    except Exception as e:
        return _error_response(e)
    logger.info(
        f"Provisioning logs served: user={request.user.id} infra={infra_id} bytes={len(payload['logs'].encode())}"
    )
    return Response(payload)
