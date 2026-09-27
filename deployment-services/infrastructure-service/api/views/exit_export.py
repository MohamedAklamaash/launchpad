import logging
import time
import uuid

from api.services.exit_export import (
    ExitExportForbidden,
    ExitExportNotFound,
    ExitExportService,
    ExitExportTooLarge,
    ExitExportUpstreamError,
)
from api.services.exit_export_audit import record_access
from api.services.platform_dns.teardown import (
    DnsTeardownPending,
    request_and_await_dns_teardown,
)
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from shared.errors.exception import HttpError
from shared.ratelimit.budget import customer_call_budget

logger = logging.getLogger(__name__)

exit_export_service = ExitExportService()

RATE_LIMIT_BUCKET = "exit_export"
PER_INFRA_BUCKET = "exit_export_infra"
COMPLETE_EXIT_BUCKET = "complete_exit"
REAUTH_ERROR_CODE = "reauth_required"
# Small negative allowance for clock skew between auth-service (which stamps `auth_time`)
# and this service's own clock — never a reason to widen the freshness window itself.
REAUTH_CLOCK_SKEW_SECONDS = 60


def _client_ip(request: HttpRequest) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _no_store(response: Response, request_id: str) -> Response:
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Request-Id"] = request_id
    return response


def _reauth_ok(user) -> bool:
    """The JWT's `auth_time` — the original interactive login (password/OTP/GitHub OAuth),
    never advanced by a refresh — must be within the last EXIT_EXPORT_REAUTH_MAX_AGE_SECONDS.

    `iat` is deliberately never consulted, not even as a fallback: auth-service stamps a
    fresh `iat` on every refresh-token exchange too, so accepting it here would let a
    caller holding only a stolen refresh token (never the original credentials) satisfy
    this check indefinitely by refreshing right before each call — exactly the bypass this
    field exists to close. A token with no `auth_time` (issued by an auth-service build
    predating this claim) is treated as stale, not exempt — see plan/F6-exit-export.md
    Decisions."""
    auth_time = user.get("auth_time") if hasattr(user, "get") else None
    if auth_time is None:
        return False
    try:
        auth_time = float(auth_time)
    except (TypeError, ValueError):
        return False
    age = timezone.now().timestamp() - auth_time
    return -REAUTH_CLOCK_SKEW_SECONDS <= age <= settings.EXIT_EXPORT_REAUTH_MAX_AGE_SECONDS


class ExitExportErrorSerializer(serializers.Serializer):
    error = serializers.CharField()
    code = serializers.CharField(required=False)


@extend_schema(
    summary="Exit export: a continuity handover archive for this infrastructure",
    description=(
        "Owner only — invited users get 403. Streams a zip: README + revocation "
        "instructions, a regenerated Terraform bundle pointing at this infrastructure's "
        "existing state, cleaned ECS task-definitions / Kubernetes manifests with every "
        "env value redacted, the CI buildspec, and a per-app GitHub webhook removal list. "
        "Requires a JWT issued within the last few minutes (401 reauth_required "
        "otherwise). Strictly read-only against the customer's AWS account."
    ),
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    responses={200: OpenApiTypes.BINARY, 401: ExitExportErrorSerializer, 403: ExitExportErrorSerializer,
               404: ExitExportErrorSerializer, 429: ExitExportErrorSerializer},
    methods=["GET"],
)
@csrf_exempt
@api_view(["GET"])
def exit_export(request: HttpRequest, infra_id):
    request_id = str(uuid.uuid4())
    client_ip = _client_ip(request)
    started = time.monotonic()

    def _deny(status_code, body, *, infrastructure_id=None):
        record_access(
            request_id=request_id, user_id=request.user.id, infrastructure_id=infrastructure_id,
            action="export", status_code=status_code,
            duration_ms=int((time.monotonic() - started) * 1000), client_ip=client_ip,
        )
        return _no_store(Response(body, status=status_code), request_id)

    try:
        retry_after = customer_call_budget(
            request.user.id, RATE_LIMIT_BUCKET,
            limit=settings.RATE_BUDGET_EXIT_EXPORT_LIMIT,
            window=settings.RATE_BUDGET_EXIT_EXPORT_WINDOW_SECONDS,
        )
    except HttpError as e:
        return _deny(e.status_code, {"error": e.message})
    if retry_after is not None:
        response = _deny(status.HTTP_429_TOO_MANY_REQUESTS,
                          {"error": "Too many requests for this operation, try again later"})
        response["Retry-After"] = str(retry_after)
        return response

    if not _reauth_ok(request.user):
        return _deny(status.HTTP_401_UNAUTHORIZED,
                      {"error": "Please log in again to continue", "code": REAUTH_ERROR_CODE})

    try:
        infra = exit_export_service.get_owned_infra(request.user.id, infra_id)
    except ExitExportNotFound:
        return _deny(status.HTTP_404_NOT_FOUND, {"error": "Infrastructure not found"})
    except ExitExportForbidden:
        return _deny(status.HTTP_403_FORBIDDEN,
                      {"error": "Only the infrastructure owner can export this infrastructure"},
                      infrastructure_id=infra_id)

    try:
        infra_retry_after = customer_call_budget(
            str(infra.id), PER_INFRA_BUCKET,
            limit=1, window=settings.EXIT_EXPORT_PER_INFRA_WINDOW_SECONDS,
        )
    except HttpError as e:
        return _deny(e.status_code, {"error": e.message}, infrastructure_id=infra.id)
    if infra_retry_after is not None:
        response = _deny(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"error": "An export was already generated for this infrastructure recently"},
            infrastructure_id=infra.id,
        )
        response["Retry-After"] = str(infra_retry_after)
        return response

    try:
        zip_bytes, app_count = exit_export_service.build(infra, request.headers.get("Authorization"))
    except ExitExportUpstreamError:
        logger.warning("exit_export upstream error", extra={"request_id": request_id, "infra_id": str(infra.id)})
        return _deny(status.HTTP_502_BAD_GATEWAY,
                      {"error": "Could not reach application data"}, infrastructure_id=infra.id)
    except ExitExportTooLarge:
        logger.error("exit_export archive too large", extra={"request_id": request_id, "infra_id": str(infra.id)})
        return _deny(status.HTTP_500_INTERNAL_SERVER_ERROR,
                      {"error": "Export too large to generate"}, infrastructure_id=infra.id)
    except Exception as e:
        logger.error("Unhandled exit_export error: %s", type(e).__name__, extra={"request_id": request_id})
        return _deny(status.HTTP_500_INTERNAL_SERVER_ERROR, {"error": "Internal error"}, infrastructure_id=infra.id)

    record_access(
        request_id=request_id, user_id=request.user.id, infrastructure_id=infra.id, action="export",
        status_code=200, app_count=app_count, total_bytes=len(zip_bytes),
        duration_ms=int((time.monotonic() - started) * 1000), client_ip=client_ip,
    )
    response = HttpResponse(zip_bytes, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="exit-export-{infra.id}.zip"'
    response["Cache-Control"] = "no-store"
    response["X-Request-Id"] = request_id
    return response


class CompleteExitRequestSerializer(serializers.Serializer):
    confirm = serializers.BooleanField()


class CompleteExitResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    dns_teardown = serializers.CharField()


@extend_schema(
    summary="Complete exit: request platform DNS teardown and mark this infrastructure exited",
    description=(
        "Owner only. Distinct from the exit export (which is read-only): this requests "
        "removal of this infrastructure's platform DNS records and marks it exited. "
        "Requires {\"confirm\": true} in the body. Idempotent — a second call after "
        "success returns 409."
    ),
    request=CompleteExitRequestSerializer,
    responses={200: CompleteExitResponseSerializer, 202: CompleteExitResponseSerializer,
               400: ExitExportErrorSerializer, 403: ExitExportErrorSerializer,
               404: ExitExportErrorSerializer, 409: ExitExportErrorSerializer},
    methods=["POST"],
)
@csrf_exempt
@api_view(["POST"])
def infrastructure_complete_exit(request: HttpRequest, infra_id):
    request_id = str(uuid.uuid4())
    client_ip = _client_ip(request)
    started = time.monotonic()

    def _deny(status_code, body, *, infrastructure_id=None):
        record_access(
            request_id=request_id, user_id=request.user.id, infrastructure_id=infrastructure_id,
            action="complete_exit", status_code=status_code,
            duration_ms=int((time.monotonic() - started) * 1000), client_ip=client_ip,
        )
        return _no_store(Response(body, status=status_code), request_id)

    try:
        retry_after = customer_call_budget(
            request.user.id, COMPLETE_EXIT_BUCKET,
            limit=settings.RATE_BUDGET_COMPLETE_EXIT_LIMIT,
            window=settings.RATE_BUDGET_COMPLETE_EXIT_WINDOW_SECONDS,
        )
    except HttpError as e:
        return _deny(e.status_code, {"error": e.message})
    if retry_after is not None:
        response = _deny(status.HTTP_429_TOO_MANY_REQUESTS,
                          {"error": "Too many requests for this operation, try again later"})
        response["Retry-After"] = str(retry_after)
        return response

    if not _reauth_ok(request.user):
        return _deny(status.HTTP_401_UNAUTHORIZED,
                      {"error": "Please log in again to continue", "code": REAUTH_ERROR_CODE})

    try:
        infra = exit_export_service.get_owned_infra(request.user.id, infra_id)
    except ExitExportNotFound:
        return _deny(status.HTTP_404_NOT_FOUND, {"error": "Infrastructure not found"})
    except ExitExportForbidden:
        return _deny(status.HTTP_403_FORBIDDEN,
                      {"error": "Only the infrastructure owner can complete exit"}, infrastructure_id=infra_id)

    body = CompleteExitRequestSerializer(data=request.data)
    if not body.is_valid() or not body.validated_data["confirm"]:
        return _deny(status.HTTP_400_BAD_REQUEST,
                      {"error": "Set confirm: true to complete exit"}, infrastructure_id=infra.id)

    if infra.exited_at is not None:
        return _deny(status.HTTP_409_CONFLICT,
                      {"error": "This infrastructure has already exited"}, infrastructure_id=infra.id)

    try:
        request_and_await_dns_teardown(
            infra.id, timeout_seconds=settings.EXIT_COMPLETE_DNS_TEARDOWN_TIMEOUT_SECONDS,
        )
    except DnsTeardownPending:
        # The reconcile request was already published (request_and_await_dns_teardown
        # publishes before it starts polling) — fail closed on marking exited, matching
        # F1b's own H2 pattern, rather than declaring victory while a wildcard/edge record
        # might still resolve. A retry is safe: the underlying request is idempotent.
        logger.warning("exit_export DNS teardown not confirmed in time",
                        extra={"request_id": request_id, "infra_id": str(infra.id)})
        return _deny(
            status.HTTP_202_ACCEPTED,
            {"status": "exit_pending", "dns_teardown": "pending",
             "message": "DNS teardown requested but not yet confirmed — retry shortly"},
            infrastructure_id=infra.id,
        )
    except Exception as e:
        # An unhandled failure here must still be audited like every other outcome — the
        # same reasoning as the export view's generic except.
        logger.error("Unhandled complete_exit error: %s", type(e).__name__, extra={"request_id": request_id})
        return _deny(status.HTTP_500_INTERNAL_SERVER_ERROR,
                      {"error": "Internal error"}, infrastructure_id=infra.id)

    # F1b part 3b: the customer keeps their own AWS resources on exit (this is not an
    # infra destroy), but Launchpad stops managing routing for it — every custom domain's
    # ALB rules/SNI cert/ACM cert must go too. Best-effort and never gates exited_at: a
    # failure here must never block the platform-DNS teardown outcome above (H2), which is
    # what this endpoint is actually accountable for confirming.
    try:
        from api.services.custom_domain_service import CustomDomainService
        CustomDomainService().teardown_for_infrastructure(infra)
    except Exception:
        logger.warning("custom-domain teardown failed during complete_exit for %s (non-fatal)",
                        infra.id, exc_info=True)

    infra.exited_at = timezone.now()
    infra.save(update_fields=["exited_at"])
    record_access(
        request_id=request_id, user_id=request.user.id, infrastructure_id=infra.id, action="complete_exit",
        status_code=200, duration_ms=int((time.monotonic() - started) * 1000), client_ip=client_ip,
    )
    return _no_store(Response({"status": "exited", "dns_teardown": "confirmed"}), request_id)
