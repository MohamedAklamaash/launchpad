from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from api.common.utils.jwt import JWTUser, decode_jwt
from api.common.env.application import app_config
import logging

logger = logging.getLogger(__name__)

class JWTAuthentication(BaseAuthentication):
    """
    Supports both standard JWT Bearer tokens and X-INTERNAL-TOKEN for gateway access.
    """
    def authenticate(self, request):
        internal_token = request.headers.get("X-INTERNAL-TOKEN")
        if not internal_token:
            raise AuthenticationFailed("Internal token is required")
        if internal_token != app_config.internal_api_token:
            raise AuthenticationFailed("Invalid internal token")

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
            raise AuthenticationFailed(str(e))
