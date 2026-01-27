from django.http import JsonResponse

from shared.utils.jwt import JWTUser, decode_jwt
from api.common.envs.application import app_config
import logging

logger = logging.getLogger(__name__)

EXCLUDED_PREFIXES = ["/admin", "/static/", "/favicon.ico"]

class JWTAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/" or any(request.path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            return self.get_response(request)

        auth_header = request.headers.get("Authorization")
        logger.info(f"Authorization header: {auth_header}")
        if not auth_header:
            raise Exception("Authorization header is required")

        if not auth_header.startswith("Bearer "):
            raise Exception("Invalid authorization header")

        token = auth_header.split(" ", 1)[1]

        try:
            request.user = decode_jwt(token, app_config.jwt_secret)

        except Exception as e:
            logger.error(f"JWT Verification Error: {e}")
            return JsonResponse(
                {
                    "message": "Unauthorized: Invalid or expired token",
                    "details": str(e),
                },
                status=401,
            )

        return self.get_response(request)
