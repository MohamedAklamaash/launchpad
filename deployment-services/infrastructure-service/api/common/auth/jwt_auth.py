from rest_framework import authentication
from rest_framework import exceptions
import logging
from django.conf import settings

logger = logging.getLogger(__name__)
exempt_paths = getattr(settings, "INTERNAL_AUTH_EXEMPT_PATHS", [])

class MiddlewareJWTAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        if any(request.path == exempt or request.path.startswith(exempt) for exempt in exempt_paths):
            return None
        django_request = request._request
        user = getattr(django_request, 'user', None)
        
        if user and hasattr(user, 'is_authenticated') and user.is_authenticated:
            logger.info(f"DRF authenticated user: {user}")
            return (user, None)
            
        logger.warning(f"DRF authentication failed. User on request: {user}")
        return None
