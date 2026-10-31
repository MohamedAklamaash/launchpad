import logging

from api.models.user import User
from django.conf import settings
from django.http import JsonResponse

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
            if settings.ORGS_ENABLED:
                # Swapping in the DB row drops the JWT's claims. OrgPermissions.get_role
                # reads org_id and account_id off request.user, so carry the
                # signature-verified values over or every org check resolves to no role.
                req_user.org_id = getattr(request.user, "org_id", None)
                req_user.account_id = getattr(request.user, "account_id", None)
            request.user = req_user
        except User.DoesNotExist:
            logger.warning(f"User {user_id} authenticated via JWT but not found in DB")
            return JsonResponse({"message": "User not synchronized"}, status=403)

        return self.get_response(request)
