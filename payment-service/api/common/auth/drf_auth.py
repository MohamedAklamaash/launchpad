from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from shared.utils.jwt import decode_jwt
from api.common.env.application import app_config

class JWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
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
            raise AuthenticationFailed(str(e))
