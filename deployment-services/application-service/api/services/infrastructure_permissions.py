from shared.enums.user_role import UserRole
import logging
from api.models.user import User

logger = logging.getLogger(__name__)


class InfrastructurePermissions:
    @staticmethod
    def get_user_role(infrastructure, user_id):
        if str(infrastructure.user_id) == str(user_id):
            return UserRole.SUPER_ADMIN
        if infrastructure.invited_users.filter(id=user_id).exists():
            return UserRole.USER
        try:
            user = User.objects.get(id=user_id)
            if user.invited_by and str(user.invited_by) == str(infrastructure.user_id):
                return UserRole.USER
        except User.DoesNotExist:
            pass
        return None

    @staticmethod
    def can_create_application(infrastructure, user_id):
        return InfrastructurePermissions.get_user_role(infrastructure, user_id) in [UserRole.SUPER_ADMIN, UserRole.ADMIN]

    @staticmethod
    def can_update_application(infrastructure, user_id):
        return InfrastructurePermissions.get_user_role(infrastructure, user_id) in [UserRole.SUPER_ADMIN, UserRole.ADMIN]

    @staticmethod
    def can_delete_application(infrastructure, user_id):
        return InfrastructurePermissions.get_user_role(infrastructure, user_id) in [UserRole.SUPER_ADMIN, UserRole.ADMIN]

    @staticmethod
    def can_view_application(infrastructure, user_id):
        return InfrastructurePermissions.get_user_role(infrastructure, user_id) is not None

    @staticmethod
    def can_update_infrastructure(infrastructure, user_id):
        return InfrastructurePermissions.get_user_role(infrastructure, user_id) == UserRole.SUPER_ADMIN

    @staticmethod
    def can_delete_infrastructure(infrastructure, user_id):
        return InfrastructurePermissions.get_user_role(infrastructure, user_id) == UserRole.SUPER_ADMIN

    @staticmethod
    def can_invite_users(infrastructure, user_id):
        return InfrastructurePermissions.get_user_role(infrastructure, user_id) == UserRole.SUPER_ADMIN
