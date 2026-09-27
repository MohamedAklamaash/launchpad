"""Customer custom-domain API — claim/verify/list/delete (F1b part 3b).

Owner-only for every write (claim/verify/delete), matching api/views/database.py's authz
ladder; list is readable by the infra owner or an invited member (CustomDomainService's
own get_by_id scoping). UUID path params are pre-validated by CustomDomainService before
any query, so a malformed id can't reach filter(id=...) and 500.
"""
import logging

from api.services.custom_domain_service import (
    CustomDomainConflictError,
    CustomDomainService,
)
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from shared.ratelimit import rate_limited

logger = logging.getLogger(__name__)

custom_domain_service = CustomDomainService()


class DnsRecordSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField()
    value = serializers.CharField(allow_null=True)


class CustomDomainResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    application_id = serializers.UUIDField(allow_null=True)
    hostname = serializers.CharField()
    status = serializers.ChoiceField(choices=['PENDING', 'VALIDATED', 'DISABLED'])
    expires_at = serializers.DateTimeField()
    last_verified_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    ownership_txt = DnsRecordSerializer()
    cname_to_edge = DnsRecordSerializer()
    acm_validation = DnsRecordSerializer(allow_null=True)


class CustomDomainClaimSerializer(serializers.Serializer):
    application_id = serializers.UUIDField()
    hostname = serializers.CharField(max_length=255)


class CustomDomainErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


def _edge_target(infra) -> str | None:
    if not infra.dns_label:
        return None
    from api.services.platform_dns import naming
    try:
        return naming.edge_record_name(infra.dns_label, settings.PLATFORM_BASE_DOMAIN)
    except Exception:
        logger.warning("could not compute edge target for infra %s", infra.id, exc_info=True)
        return None


def _serialize(domain, infra, *, ownership_token: str | None = None, validation_record: dict | None = None):
    if validation_record is None and domain.status == 'PENDING':
        validation_record = custom_domain_service.backfill_validation_record(infra, domain)

    return {
        "id": domain.id,
        "application_id": domain.application_id,
        "hostname": domain.hostname,
        "status": domain.status,
        "expires_at": domain.expires_at,
        "last_verified_at": domain.last_verified_at,
        "created_at": domain.created_at,
        "ownership_txt": {
            "name": f"_launchpad-challenge.{domain.hostname}",
            "type": "TXT",
            "value": ownership_token,
        },
        "cname_to_edge": {
            "name": domain.hostname,
            "type": "CNAME",
            "value": _edge_target(infra),
        },
        "acm_validation": (
            {"name": validation_record["name"], "type": "CNAME", "value": validation_record["value"]}
            if validation_record else None
        ),
    }


def _error_response(e: Exception):
    if isinstance(e, LookupError):
        return Response({"error": str(e) or "Not found"}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(e, PermissionError):
        return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(e, CustomDomainConflictError):
        return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)
    if isinstance(e, ValueError):
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    logger.error("Unhandled custom domain view error", exc_info=e)
    return Response({"error": "Internal error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="List custom domains claimed on an infrastructure",
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    responses={200: CustomDomainResponseSerializer(many=True)},
    methods=["GET"],
)
@extend_schema(
    summary="Claim a custom domain",
    description=(
        "Owner only. Requests a per-domain ACM certificate and returns the DNS records "
        "the customer must publish in their own DNS: an ownership TXT (shown once — save "
        "it now), the ACM validation CNAME, and a CNAME to the platform edge. EKS "
        "infrastructures are refused (host mode is not yet enabled there)."
    ),
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    request=CustomDomainClaimSerializer,
    responses={201: CustomDomainResponseSerializer, 400: CustomDomainErrorSerializer,
               403: CustomDomainErrorSerializer, 409: CustomDomainErrorSerializer},
    methods=["POST"],
)
@csrf_exempt
@api_view(['GET', 'POST'])
@rate_limited('custom_domains', limit=settings.RATE_BUDGET_CUSTOM_DOMAINS_LIMIT,
              window=settings.RATE_BUDGET_CUSTOM_DOMAINS_WINDOW_SECONDS)
def custom_domain_list_create(request, infra_id):
    if request.method == 'GET':
        try:
            infra, domains = custom_domain_service.list_domains(request.user.id, infra_id)
            return Response([_serialize(domain, infra) for domain in domains])
        except Exception as e:
            return _error_response(e)

    body = CustomDomainClaimSerializer(data=request.data)
    if not body.is_valid():
        return Response({"error": "Invalid request", "details": body.errors}, status=status.HTTP_400_BAD_REQUEST)

    try:
        domain, token, validation_record = custom_domain_service.claim_domain(
            request.user.id, infra_id, body.validated_data["application_id"],
            body.validated_data["hostname"], authorization_header=request.headers.get("Authorization"),
        )
        return Response(
            _serialize(domain, domain.infrastructure, ownership_token=token, validation_record=validation_record),
            status=status.HTTP_201_CREATED,
        )
    except Exception as e:
        return _error_response(e)


@extend_schema(
    summary="Delete (or cancel) a custom domain",
    description=(
        "Owner only. A PENDING claim is removed outright; a VALIDATED domain is detached "
        "from the ALB, has its certificate deleted, and is marked DISABLED (kept for "
        "audit history — the hostname becomes claimable again immediately)."
    ),
    parameters=[
        OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH),
        OpenApiParameter("domain_id", OpenApiTypes.STR, OpenApiParameter.PATH),
    ],
    responses={200: CustomDomainResponseSerializer, 403: CustomDomainErrorSerializer, 404: CustomDomainErrorSerializer},
    methods=["DELETE"],
)
@csrf_exempt
@api_view(['DELETE'])
@rate_limited('custom_domains', limit=settings.RATE_BUDGET_CUSTOM_DOMAINS_LIMIT,
              window=settings.RATE_BUDGET_CUSTOM_DOMAINS_WINDOW_SECONDS)
def custom_domain_detail(request, infra_id, domain_id):
    try:
        domain = custom_domain_service.delete_domain(request.user.id, infra_id, domain_id)
        return Response(_serialize(domain, domain.infrastructure))
    except Exception as e:
        return _error_response(e)


@extend_schema(
    summary="Verify a custom domain's ownership TXT and activate it",
    description=(
        "Owner only. Checks the authoritative TXT record, the ACM certificate's ISSUED "
        "status, and the ALB's SNI certificate cap, all under this infrastructure's row "
        "lock — a 409 here means the cap is reached or another account just validated the "
        "same hostname first; retry after resolving either."
    ),
    parameters=[
        OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH),
        OpenApiParameter("domain_id", OpenApiTypes.STR, OpenApiParameter.PATH),
    ],
    responses={200: CustomDomainResponseSerializer, 400: CustomDomainErrorSerializer,
               403: CustomDomainErrorSerializer, 404: CustomDomainErrorSerializer,
               409: CustomDomainErrorSerializer},
    methods=["POST"],
)
@csrf_exempt
@api_view(['POST'])
@rate_limited('custom_domains', limit=settings.RATE_BUDGET_CUSTOM_DOMAINS_LIMIT,
              window=settings.RATE_BUDGET_CUSTOM_DOMAINS_WINDOW_SECONDS)
def custom_domain_verify(request, infra_id, domain_id):
    try:
        domain = custom_domain_service.verify_domain(request.user.id, infra_id, domain_id)
        return Response(_serialize(domain, domain.infrastructure))
    except Exception as e:
        return _error_response(e)
