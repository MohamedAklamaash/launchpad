"""Internal-only endpoints for custom-domain ALB wiring (F1b part 3b).

Called same-origin, machine-to-machine, by infrastructure-service — never exposed through
the gateway, and never carrying a forwarded user JWT (unlike exit_inventory.py's
export_inventory, which re-checks ownership against its own copy of Infrastructure because
it forwards the caller's own token). infrastructure-service already did the owner check,
the row-lock, the TXT check, and the ACM ISSUED check before calling attach/detach — this
is purely "do the AWS side effect infrastructure-service's DB transaction is deciding on",
authenticated only by X-INTERNAL-TOKEN (see shared/middleware/internal_auth.py) with no
user context at all, the same way a RabbitMQ consumer wouldn't re-check user auth either.
These exact paths are listed in shared/middleware/authentication.py's EXEMPT_EXACT_PATHS so
JWTAuthMiddleware doesn't demand an Authorization header that will never be sent.
"""
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.services.custom_domain_routing import (
    CustomDomainAttachError,
    CustomDomainConflict,
    CustomDomainNotFound,
    CustomDomainNotReady,
    SniCapReached,
    attach_custom_domain,
    detach_custom_domain,
)


class CustomDomainAttachRequestSerializer(serializers.Serializer):
    infrastructure_id = serializers.UUIDField()
    application_id = serializers.UUIDField()
    hostname = serializers.CharField(max_length=255)
    cert_arn = serializers.CharField(max_length=512)


class CustomDomainDetachRequestSerializer(serializers.Serializer):
    hostname = serializers.CharField(max_length=255)


_ERROR_STATUS = {
    CustomDomainNotFound: status.HTTP_404_NOT_FOUND,
    CustomDomainNotReady: status.HTTP_409_CONFLICT,
    CustomDomainConflict: status.HTTP_409_CONFLICT,
    SniCapReached: status.HTTP_409_CONFLICT,
}


@extend_schema(exclude=True)
@csrf_exempt
@api_view(['POST'])
def custom_domain_attach(request):
    """Internal only. Attaches `cert_arn` to the infra's 443 listener (subject to the SNI
    cap) and creates the app's host-forward/host-redirect rules. Idempotent for a hostname
    already attached to the same (infrastructure, application)."""
    body = CustomDomainAttachRequestSerializer(data=request.data)
    if not body.is_valid():
        return Response({"error": "Invalid request", "details": body.errors}, status=status.HTTP_400_BAD_REQUEST)

    try:
        result = attach_custom_domain(**body.validated_data)
    except CustomDomainAttachError as e:
        return Response({"error": str(e), "code": type(e).__name__},
                         status=_ERROR_STATUS.get(type(e), status.HTTP_400_BAD_REQUEST))
    return Response(result, status=status.HTTP_200_OK)


@extend_schema(exclude=True)
@csrf_exempt
@api_view(['POST'])
def custom_domain_detach(request):
    """Internal only. Idempotent — detaching a hostname with no CustomDomainRoute row is
    a 200, not a 404: infrastructure-service calls this on every disable regardless of
    whether the attach ever actually succeeded."""
    body = CustomDomainDetachRequestSerializer(data=request.data)
    if not body.is_valid():
        return Response({"error": "Invalid request", "details": body.errors}, status=status.HTTP_400_BAD_REQUEST)

    detached = detach_custom_domain(body.validated_data["hostname"])
    return Response({"detached": detached}, status=status.HTTP_200_OK)
