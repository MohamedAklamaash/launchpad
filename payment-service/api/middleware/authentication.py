from rest_framework.authentication import BaseAuthentication
from api.common.utils.jwt import decode_jwt
from api.common.env.application import app_config
from api.common.errors.exception import HttpError
import logging

logger = logging.getLogger(__name__)

EXEMPT_PATHS = [
    "/api/v1/webhook/",
    "/api/v1/success/",
    "/api/v1/cancel/",
    "/api/v1/healthz/",
    "/api/v1/liveness/",
    "/api/v1/readiness/"
]

class JWTAuthentication(BaseAuthentication):
    """
    Supports both standard JWT Bearer tokens and X-INTERNAL-TOKEN for gateway access.
    """
    def authenticate(self, request):
        path = request.path
        if not path.endswith('/'):
            path += '/'

        if any(path == p for p in EXEMPT_PATHS) or path.startswith("/admin/"):
            return None

        internal_token = request.headers.get("X-INTERNAL-TOKEN")
        
        if not internal_token:
            logger.warning(f"Missing X-INTERNAL-TOKEN for path: {path}")
            raise HttpError("Internal token is required", status_code=401)
            
        if internal_token != app_config.internal_api_token:
            logger.warning(f"Invalid X-INTERNAL-TOKEN for path: {path}")
            raise HttpError("Invalid internal token", status_code=401)
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        if not auth_header.lower().startswith("bearer "):
            return None

        token = auth_header.split(" ", 1)[1]
        try:
            user = decode_jwt(token, app_config.jwt_secret)
            return (user, token)
        except Exception as e:
            logger.error(f"JWT Verification Error (DRF): {e}")
            return None
