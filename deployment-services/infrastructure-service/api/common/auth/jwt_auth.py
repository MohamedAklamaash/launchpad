from rest_framework import authentication
from rest_framework import exceptions
import logging

logger = logging.getLogger(__name__)

class MiddlewareJWTAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        django_request = request._request
        user = getattr(django_request, 'user', None)
        
        if user and hasattr(user, 'is_authenticated') and user.is_authenticated:
            logger.info(f"DRF authenticated user: {user}")
            return (user, None)
            
        logger.warning(f"DRF authentication failed. User on request: {user}")
        return None
