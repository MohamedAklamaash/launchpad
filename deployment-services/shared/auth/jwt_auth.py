from rest_framework import authentication
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class MiddlewareJWTAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        django_request = request._request
        user = getattr(django_request, 'user', None)
        
        if user and hasattr(user, 'is_authenticated') and user.is_authenticated:
            logger.info(f"DRF authenticated user from middleware: {user}")
            return (user, None)
            
        return None
