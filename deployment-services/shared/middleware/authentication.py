from django.conf import settings
from shared.utils.jwt import decode_jwt
from shared.errors.exception import HttpError
import logging
from django.http import JsonResponse

def my_view(request):
    data = {'message': 'Hello, world!', 'status': 'success'}
    return JsonResponse(data)
    
logger = logging.getLogger(__name__)

EXCLUDED_PREFIXES = ["/admin", "/static/", "/favicon.ico", "/health", "/api/v1/healthz", "/api/v1/liveness", "/api/v1/readiness", "/api/v1/docs", "/api/v1/schema", "/api/v1/webhooks/"]

# Exact-match exemptions for callback/webhook routes — startswith would over-exempt
# anything sharing the prefix (e.g. /api/v1/payments/webhook/foo).
EXEMPT_EXACT_PATHS = ["/api/v1/infrastructures/onboarding/callback/", "/api/v1/payments/webhook/", "/api/v1/payments/success/", "/api/v1/payments/cancel/"]

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
            logger.info(f"Authorization header: {auth_header}")
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
        except Exception as e:
            logger.exception(f"Unexpected error in JWTAuthMiddleware: {e}")
            return JsonResponse(
                {"message": "Internal Server Error", "details": str(e)},
                status=500
            )

        return self.get_response(request)
