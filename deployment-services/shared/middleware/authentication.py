from django.http import JsonResponse
from shared.utils.jwt import JWTUser, decode_jwt
from api.common.envs.application import app_config
from shared.errors.exception import HttpError
import logging

logger = logging.getLogger(__name__)

EXCLUDED_PREFIXES = ["/admin", "/static/", "/favicon.ico"]

class JWTAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/" or any(request.path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            return self.get_response(request)

        try:
            auth_header = request.headers.get("Authorization")
            logger.info(f"Authorization header: {auth_header}")
            if not auth_header:
                raise HttpError("Authorization header is required", status_code=401)

            if not auth_header.startswith("Bearer "):
                raise HttpError("Invalid authorization header", status_code=401)

            token = auth_header.split(" ", 1)[1]
            request.user = decode_jwt(token, app_config.jwt_secret)

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
