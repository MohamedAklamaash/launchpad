"""Internal-only read endpoint for another service (or an operator tool calling through
it) to check an infrastructure's exit status without a user JWT.

H2 (hardening) RECOMMENDED 2 — see plan/H-hardening.md: application-service's
`clear_infrastructure_exited` management command uses this to confirm, against
infrastructure-service's own authoritative row, that an infrastructure is genuinely NOT
exited before clearing a bad/forged `exited_at` on its own read-model mirror. No forwarded
user JWT: pure machine-to-machine, authenticated only by X-INTERNAL-TOKEN (this exact path
is in shared/middleware/authentication.py's EXEMPT_EXACT_PATHS, same as
custom_domain_internal.py's endpoint) — deliberately NOT in INTERNAL_AUTH_EXEMPT_PATHS, so
the internal-token check still applies.

Fixed literal path, `infrastructure_id` in the query string rather than a URL path
segment — EXEMPT_EXACT_PATHS only matches fixed literal paths, no path-param UUID (see
that list's own docstring)."""
from api.models.infrastructure import Infrastructure
from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response


class ExitStatusQuerySerializer(serializers.Serializer):
    infrastructure_id = serializers.UUIDField()


@api_view(['GET'])
@csrf_exempt
def infrastructure_exit_status(request):
    query = ExitStatusQuerySerializer(data=request.query_params)
    if not query.is_valid():
        return Response({"error": "Invalid request", "details": query.errors}, status=status.HTTP_400_BAD_REQUEST)

    infra = Infrastructure.objects.filter(id=query.validated_data["infrastructure_id"]).first()
    if infra is None:
        return Response({"error": "Infrastructure not found"}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        "infrastructure_id": str(infra.id),
        "exited_at": infra.exited_at.isoformat() if infra.exited_at else None,
    }, status=status.HTTP_200_OK)
