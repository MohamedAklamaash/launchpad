"""Internal-only endpoint for a Nuke infrastructure run (infrastructure-service). Same
trust-boundary shape as custom_domains_internal.py's attach/detach: no user JWT is ever
sent, authenticated only by X-INTERNAL-TOKEN (shared/middleware/internal_auth.py),
listed in shared/middleware/authentication.py's EXEMPT_EXACT_PATHS so JWTAuthMiddleware
doesn't demand an Authorization header that will never arrive. infrastructure-service
already did the owner check and the typed-name confirmation before calling this — this
endpoint does the AWS-side effect infrastructure-service's nuke worker is deciding on.
"""
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

from api.services.application_service import ApplicationService


class NukeApplicationsRequestSerializer(serializers.Serializer):
    infrastructure_id = serializers.UUIDField()


# Machine-to-machine only: no user JWT is ever sent, so the default IsAuthenticated
# permission turned every call into a 403 (seen on real AWS for the nuke call). The
# trust boundary is X-INTERNAL-TOKEN, enforced by shared/middleware/internal_auth.py
# on every /api/v1/internal/ path; the gateway exposes no route to them.
@extend_schema(exclude=True)
@csrf_exempt
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def nuke_applications(request):
    """Internal only. Force-deletes every Application on this infrastructure — AWS/
    Kubernetes cleanup then the DB row, per app, synchronously — and returns a summary.
    Idempotent: an infrastructure with no remaining Application rows returns
    deleted_count=0 and is a 200, not a 404."""
    body = NukeApplicationsRequestSerializer(data=request.data)
    if not body.is_valid():
        return Response({"error": "Invalid request", "details": body.errors}, status=status.HTTP_400_BAD_REQUEST)

    result = ApplicationService().nuke_applications_for_infrastructure(
        str(body.validated_data["infrastructure_id"])
    )
    return Response(result, status=status.HTTP_200_OK)
