from django.http import JsonResponse

from shared.utils.jwt import JWTUser, decode_jwt
from api.common.envs.application import ApplicationConfig

EXCLUDED_PREFIXES = ["/admin", "/static/", "/favicon.ico"]

class JWTAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/" or any(request.path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            return self.get_response(request)

        auth_header = request.headers.get("Authorization")

        if not auth_header:
            raise Exception("Authorization header is required")

        if not auth_header.startswith("Bearer "):
            raise Exception("Invalid authorization header")

        token = auth_header.split(" ", 1)[1]

        try:
            payload = decode_jwt(token, ApplicationConfig.jwt_secret)

            request.user = JWTUser(
                sub=payload["sub"],
                email=payload["email"],
                iat=payload["iat"],
                exp=payload["exp"],
            )

        except Exception:
            raise Exception("Invalid or expired token")

        return self.get_response(request)
