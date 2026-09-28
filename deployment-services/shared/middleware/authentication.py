import logging

import jwt
from django.conf import settings
from django.http import JsonResponse

from shared.errors.exception import HttpError
from shared.utils.jwt import decode_jwt

logger = logging.getLogger(__name__)

EXCLUDED_PREFIXES = ["/admin", "/static/", "/favicon.ico", "/health", "/api/v1/healthz", "/api/v1/liveness", "/api/v1/readiness", "/api/v1/docs", "/api/v1/schema", "/api/v1/webhooks/"]

# Exact-match exemptions for callback/webhook routes — startswith would over-exempt
# anything sharing the prefix (e.g. /api/v1/payments/webhook/foo).
#
# F1b part 3b (custom domains): the three internal/custom-domains/* paths are pure
# machine-to-machine calls between infrastructure-service and application-service — no
# user JWT is ever sent with them (unlike export-inventory, which forwards the caller's
# own token). Fixed literal paths, no path-param UUID, so they match here the same way
# the callback routes do. Still enforced by X-INTERNAL-TOKEN — not listed in either
# service's INTERNAL_AUTH_EXEMPT_PATHS/_PREFIXES.
EXEMPT_EXACT_PATHS = [
    "/api/v1/infrastructures/onboarding/callback/", "/api/v1/infrastructures/policy-refresh/callback/",
    "/api/v1/payments/webhook/", "/api/v1/payments/success/", "/api/v1/payments/cancel/",
    "/api/v1/internal/custom-domains/attach/", "/api/v1/internal/custom-domains/detach/",
    "/api/v1/internal/custom-domains/disable-for-application/",
    # H2 (hardening) RECOMMENDED 2: infrastructure-service's read-only exit-status lookup
    # for application-service's clear_infrastructure_exited command — same machine-to-
    # machine shape as the custom-domains paths above, still enforced by X-INTERNAL-TOKEN.
    "/api/v1/internal/infrastructures/exit-status/",
]

class JWTAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Middleware runs before APPEND_SLASH redirect, so check both forms of the path.
        if (
            request.path == "/"
            or any(request.path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
            or request.path in EXEMPT_EXACT_PATHS
            or request.path + "/" in EXEMPT_EXACT_PATHS
        ):
            return self.get_response(request)

        try:
            auth_header = request.headers.get("Authorization")
            # Never log the header value — it's a bearer JWT. Log only presence.
            logger.debug("Authorization header present: %s", bool(auth_header))
            if not auth_header:
                raise HttpError("Authorization header is required", status_code=401)

            if not auth_header.startswith("Bearer "):
                raise HttpError("Invalid authorization header", status_code=401)

            token = auth_header.split(" ", 1)[1]
            request.user = decode_jwt(token, settings.JWT_SECRET)

        except HttpError as e:
            return JsonResponse(
                {"message": e.message, "details": e.details},
                status=e.status_code
            )
        except jwt.PyJWTError:
            # Expired, malformed, invalid-signature, or a ScopedTokenRejected token —
            # never echo the exception text back to the caller.
            return JsonResponse(
                {"message": "Invalid or expired token", "details": None},
                status=401
            )
        except Exception:
            logger.exception("Unexpected error in JWTAuthMiddleware")
            return JsonResponse({"message": "Internal Server Error"}, status=500)

        return self.get_response(request)
