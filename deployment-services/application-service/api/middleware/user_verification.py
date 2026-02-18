from django.http import JsonResponse
from api.models.user import User
import logging

logger = logging.getLogger(__name__)

class UserVerificationMiddleware:
    """Verify that the JWT user exists in the local database."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not hasattr(request, 'user') or not request.user:
            return self.get_response(request)
        user_id = getattr(request.user, 'id', None)
        if not user_id:
            return self.get_response(request)

        try:
            req_user = User.objects.get(id=user_id)
            request.user = req_user
        except User.DoesNotExist:
            logger.warning(f"User {user_id} authenticated via JWT but not found in DB")
            return JsonResponse({"message": "User not synchronized"}, status=403)

        return self.get_response(request)
