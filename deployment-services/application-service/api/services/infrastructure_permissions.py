from shared.enums.user_role import UserRole
import logging

logger = logging.getLogger(__name__)


class InfrastructurePermissions:
    @staticmethod
    def get_user_role(infrastructure, user_id):
        if str(infrastructure.user_id) == str(user_id):
            return UserRole.SUPER_ADMIN
        # Authorize strictly on per-infra membership. The previous `invited_by` fallback
        # granted access to EVERY infra owned by the inviter — leaking other infras'
        # application env-vars/secrets to a user invited to just one of them.
        member = infrastructure.invited_users.filter(id=user_id).first()
        if member is None:
            return None
        # Honor the role the member was invited with (an invited ADMIN can manage this infra's
        # apps; a USER stays read-only) — the previous hardcoded USER made every invite view-only.
        # Cap at ADMIN: a non-owner is never owner-level on an infra they don't own, whatever
        # their global role happens to be.
        if member.role == UserRole.SUPER_ADMIN:
            return UserRole.ADMIN
        return member.role or UserRole.USER

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
