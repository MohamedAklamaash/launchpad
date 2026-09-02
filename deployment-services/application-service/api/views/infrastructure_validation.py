from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.models.infrastructure import Infrastructure
from api.services.infrastructure_permissions import InfrastructurePermissions
from api.services.infrastructure_validation import InfrastructureValidation


class ValidationResponseSerializer(serializers.Serializer):
    can_delete = serializers.BooleanField(help_text="Whether the infrastructure can be safely deleted")
    app_count = serializers.IntegerField(help_text="Number of active applications on this infrastructure")
    error_message = serializers.CharField(allow_null=True, help_text="Reason deletion is blocked, or null if allowed")


@extend_schema(
    summary="Check if an infrastructure can be safely deleted",
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.UUID, OpenApiParameter.PATH,
                                 description="Infrastructure UUID to check")],
    responses={200: ValidationResponseSerializer},
)
@api_view(['GET'])
def infrastructure_validation(request, infra_id):
    try:
        infra = Infrastructure.objects.get(id=infra_id)
    except Infrastructure.DoesNotExist:
        return Response({"error": "Infrastructure not found"}, status=status.HTTP_404_NOT_FOUND)
    # A non-member must not learn another tenant's app count or deletability. 404 (not 403)
    # so the endpoint never confirms an infra id the caller has no relationship to.
    if InfrastructurePermissions.get_user_role(infra, request.user.id) is None:
        return Response({"error": "Infrastructure not found"}, status=status.HTTP_404_NOT_FOUND)
    can_delete, error_message = InfrastructureValidation.can_delete_infrastructure(infra_id)
    app_count = InfrastructureValidation.get_infrastructure_apps_count(infra_id)
    return Response({"can_delete": can_delete, "app_count": app_count, "error_message": error_message})
