"""Internal-only endpoints for custom-domain ALB wiring (F1b part 3b).

Attach/detach are called same-origin, machine-to-machine, by infrastructure-service —
never exposed through the gateway, and never carrying a forwarded user JWT (unlike
exit_inventory.py's export_inventory, which re-checks ownership against its own copy of
Infrastructure because it forwards the caller's own token). infrastructure-service already
did the owner check, the row-lock, the TXT check, and the ACM ISSUED check before calling
attach/detach — this is purely "do the AWS side effect infrastructure-service's DB
transaction is deciding on", authenticated only by X-INTERNAL-TOKEN (see
shared/middleware/internal_auth.py) with no user context at all, the same way a RabbitMQ
consumer wouldn't re-check user auth either. These exact paths are listed in
shared/middleware/authentication.py's EXEMPT_EXACT_PATHS so JWTAuthMiddleware doesn't
demand an Authorization header that will never be sent.

`application_summary_for_custom_domains` is different: it DOES forward the caller's own
JWT (same pattern as export_inventory) because it needs the human owner's identity to
re-check application ownership — but returns only {id, infrastructure_id, status}, not the
full application detail `GET /applications/{id}/` would (env vars, webhook secret shape,
etc.) — infrastructure-service's claim flow has no legitimate use for any of that, and
receiving it at all would be unnecessary secrets exposure (security review RECOMMENDED).
"""
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
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
from api.services.exit_enforcement import InfrastructureExitedError, require_not_exited


class CustomDomainAttachRequestSerializer(serializers.Serializer):
    infrastructure_id = serializers.UUIDField()
    application_id = serializers.UUIDField()
    hostname = serializers.CharField(max_length=255)
    cert_arn = serializers.CharField(max_length=512)


class CustomDomainDetachRequestSerializer(serializers.Serializer):
    infrastructure_id = serializers.UUIDField()
    hostname = serializers.CharField(max_length=255)
    # Asserted by infrastructure-service, the only service whose Infrastructure row
    # carries dns_teardown_requested_at/exited_at — see
    # custom_domain_routing._is_already_detached_error. Optional so a caller that
    # predates this field still gets today's behavior (never treats AccessDenied as
    # success).
    infra_tearing_down = serializers.BooleanField(required=False, default=False)


_ERROR_STATUS = {
    CustomDomainNotFound: status.HTTP_404_NOT_FOUND,
    CustomDomainNotReady: status.HTTP_409_CONFLICT,
    CustomDomainConflict: status.HTTP_409_CONFLICT,
    SniCapReached: status.HTTP_409_CONFLICT,
}


# Machine-to-machine only: no user JWT is ever sent, so the default IsAuthenticated
# permission turned every call into a 403 (seen on real AWS for the nuke call). The
# trust boundary is X-INTERNAL-TOKEN, enforced by shared/middleware/internal_auth.py
# on every /api/v1/internal/ path; the gateway exposes no route to them.
@extend_schema(exclude=True)
@csrf_exempt
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def custom_domain_attach(request):
    """Internal only. Attaches `cert_arn` to the infra's 443 listener (subject to the SNI
    cap) and creates the app's host-forward/host-redirect rules. Idempotent for a hostname
    already attached to the same (infrastructure, application)."""
    body = CustomDomainAttachRequestSerializer(data=request.data)
    if not body.is_valid():
        return Response({"error": "Invalid request", "details": body.errors}, status=status.HTTP_400_BAD_REQUEST)

    from api.models.infrastructure import Infrastructure

    infra = Infrastructure.objects.filter(id=body.validated_data["infrastructure_id"]).first()
    try:
        require_not_exited(infra)
    except InfrastructureExitedError as e:
        return Response({"error": str(e), "code": e.code}, status=status.HTTP_409_CONFLICT)

    try:
        result = attach_custom_domain(**body.validated_data)
    except CustomDomainAttachError as e:
        return Response({"error": str(e), "code": type(e).__name__},
                         status=_ERROR_STATUS.get(type(e), status.HTTP_400_BAD_REQUEST))
    return Response(result, status=status.HTTP_200_OK)


@extend_schema(exclude=True)
@csrf_exempt
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def custom_domain_detach(request):
    """Internal only. Idempotent — detaching a hostname with no CustomDomainRoute row for
    this infrastructure is a 200, not a 404: infrastructure-service calls this on every
    disable regardless of whether the attach ever actually succeeded. Scoped by
    (infrastructure_id, hostname) — see detach_custom_domain's docstring for why hostname
    alone is not safe. A genuine AWS-side failure is a 502, not a 200: the caller
    (CustomDomainService._detach) treats anything but 200 as "not confirmed, retry" and
    must never mark the row DISABLED on a detach that didn't actually happen."""
    body = CustomDomainDetachRequestSerializer(data=request.data)
    if not body.is_valid():
        return Response({"error": "Invalid request", "details": body.errors}, status=status.HTTP_400_BAD_REQUEST)

    detached = detach_custom_domain(
        body.validated_data["infrastructure_id"], body.validated_data["hostname"],
        infra_tearing_down=body.validated_data["infra_tearing_down"],
    )
    if not detached:
        return Response({"detached": False}, status=status.HTTP_502_BAD_GATEWAY)
    return Response({"detached": True}, status=status.HTTP_200_OK)


@extend_schema(exclude=True)
@api_view(['GET'])
def application_summary_for_custom_domains(request, app_id):
    """Owner-only (re-checked against this service's own copy of Infrastructure, same as
    every other application-scoped endpoint) — called by infrastructure-service's
    custom-domain claim flow, never exposed through the gateway."""
    from api.services.application_service import ApplicationService

    try:
        app = ApplicationService().get_application_details(request.user.id, app_id)
    except (ValidationError, ValueError):
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    if app is None:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response({"id": str(app.id), "infrastructure_id": str(app.infrastructure_id), "status": app.status})
