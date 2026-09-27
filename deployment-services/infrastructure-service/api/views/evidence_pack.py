import logging

from api.services.evidence_pack import EvidencePackService
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from shared.ratelimit import rate_limited

logger = logging.getLogger(__name__)

evidence_pack_service = EvidencePackService()


def _error_response(e: Exception):
    if isinstance(e, LookupError):
        return Response({'error': str(e)}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(e, PermissionError):
        return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
    logger.error("Unhandled evidence pack view error", exc_info=e)
    return Response({'error': 'Internal error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Compliance evidence pack for an infrastructure",
    description=(
        "Owner only — invited users get 403. A zip containing the rendered IAM policy, "
        "the expected trust-policy shape, a live drift check against the customer's AWS "
        "account, and an honest limitations section. Nothing is persisted server-side."
    ),
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    responses={200: OpenApiTypes.BINARY, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    methods=["GET"],
)
@csrf_exempt
@api_view(['GET'])
@rate_limited('evidence', limit=settings.RATE_BUDGET_EVIDENCE_LIMIT, window=settings.RATE_BUDGET_EVIDENCE_WINDOW_SECONDS)
def evidence_pack(request: HttpRequest, infra_id):
    try:
        zip_bytes, infra = evidence_pack_service.get_pack(user_id=request.user.id, infra_id=infra_id)
    except Exception as e:
        return _error_response(e)
    logger.info(f"Evidence pack served: user={request.user.id} infra={infra_id} bytes={len(zip_bytes)}")
    response = HttpResponse(zip_bytes, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="evidence-pack-{infra.id}.zip"'
    return response
