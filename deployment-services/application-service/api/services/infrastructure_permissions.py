from shared.enums.user_role import UserRole
import logging

logger = logging.getLogger(__name__)


class InfrastructurePermissions:
    @staticmethod
    def get_user_role(infrastructure, user_id):
        if str(infrastructure.user_id) == str(user_id):
            return UserRole.SUPER_ADMIN
        from api.models.infrastructure_user_role import InfrastructureUserRole
        # Role is per-infra: the same user can be ADMIN here and USER elsewhere. Read the
        # membership edge, not the user's global role.
        entry = InfrastructureUserRole.objects.filter(
            infrastructure=infrastructure, user_id=user_id
        ).first()
        if entry is not None:
            # A non-owner is never owner-level on an infra they don't own.
            if entry.role == UserRole.SUPER_ADMIN:
                return UserRole.ADMIN
            return entry.role or UserRole.USER
        # Membership without a role row yet (event still in flight) stays least-privileged.
        if infrastructure.invited_users.filter(id=user_id).exists():
            return UserRole.USER
        return None

    @staticmethod
    def can_create_application(infrastructure, user_id):
        return InfrastructurePermissions.get_user_role(infrastructure, user_id) in [UserRole.SUPER_ADMIN, UserRole.ADMIN]

    @staticmethod
    def can_update_application(infrastructure, user_id):
        return InfrastructurePermissions.get_user_role(infrastructure, user_id) in [UserRole.SUPER_ADMIN, UserRole.ADMIN]

    @staticmethod
    def can_attach_database(infrastructure, user_id):
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
