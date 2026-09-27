"""Per-infrastructure application inventory for infrastructure-service's exit export
(F6). Called same-origin, forwarding the caller's own JWT — this view re-checks ownership
against this service's own copy of Infrastructure rather than trusting the caller, exactly
like every other infra-scoped endpoint in this service (see infrastructure_validation.py).
The X-INTERNAL-TOKEN requirement is the same one every other path already carries via
InternalAuthMiddleware; nothing here is exempted from it.
"""
import uuid

from django.core.exceptions import ValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from shared.enums.user_role import UserRole

from api.models.infrastructure import Infrastructure
from api.services.exit_inventory import export_data_for_infrastructure
from api.services.infrastructure_permissions import InfrastructurePermissions


@extend_schema(
    summary="Exit-export inventory for every application on an infrastructure (internal)",
    description=(
        "Owner only — called by infrastructure-service's exit-export endpoint, never "
        "exposed through the gateway. Every env value is redacted before this response "
        "is built; only key names, ARNs, and cleaned task-definition/manifest text cross "
        "this boundary."
    ),
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.UUID, OpenApiParameter.PATH)],
    responses={200: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    methods=["GET"],
)
@api_view(["GET"])
def export_inventory(request, infra_id):
    try:
        uuid.UUID(str(infra_id))
        infra = Infrastructure.objects.get(id=infra_id)
    except (Infrastructure.DoesNotExist, ValueError, ValidationError):
        return Response({"error": "Infrastructure not found"}, status=status.HTTP_404_NOT_FOUND)

    role = InfrastructurePermissions.get_user_role(infra, request.user.id)
    if role is None:
        return Response({"error": "Infrastructure not found"}, status=status.HTTP_404_NOT_FOUND)
    if role != UserRole.SUPER_ADMIN:
        return Response({"error": "Only the infrastructure owner can export"}, status=status.HTTP_403_FORBIDDEN)

    return Response(export_data_for_infrastructure(infra_id))
