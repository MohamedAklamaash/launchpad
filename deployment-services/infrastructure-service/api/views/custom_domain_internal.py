"""Internal-only endpoint notifying infrastructure-service that an application was
deleted, so its custom domains can be disabled and their certificates deleted (F1b part
3b). Called same-origin by application-service's own app-delete cleanup, after it has
already detached its own ALB rules/SNI certificate locally — mirrors
api/views/custom_domain.py's cross-service internal counterpart in application-service.
No forwarded user JWT: pure machine-to-machine, authenticated only by X-INTERNAL-TOKEN
(this exact path is in shared/middleware/authentication.py's EXEMPT_EXACT_PATHS)."""
from api.services.custom_domain_service import CustomDomainService
from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

custom_domain_service = CustomDomainService()


class DisableForApplicationRequestSerializer(serializers.Serializer):
    infrastructure_id = serializers.UUIDField()
    application_id = serializers.UUIDField()


@api_view(['POST'])
@csrf_exempt
def custom_domain_disable_for_application(request):
    body = DisableForApplicationRequestSerializer(data=request.data)
    if not body.is_valid():
        return Response({"error": "Invalid request", "details": body.errors}, status=status.HTTP_400_BAD_REQUEST)

    custom_domain_service.disable_for_application(
        body.validated_data["infrastructure_id"], body.validated_data["application_id"],
    )
    return Response({"ok": True}, status=status.HTTP_200_OK)
