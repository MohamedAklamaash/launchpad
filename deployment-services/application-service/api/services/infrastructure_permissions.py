from shared.enums.user_role import UserRole
from api.models.infrastructure_user_role import InfrastructureUserRole
import logging

logger = logging.getLogger(__name__)


class InfrastructurePermissions:
    """Check user permissions for infrastructure operations"""
    
    @staticmethod
    def get_user_role(infrastructure, user_id):
        """Get user's role for a specific infrastructure"""
        if str(infrastructure.user_id) == str(user_id):
            return UserRole.SUPER_ADMIN
        
        try:
            role_obj = InfrastructureUserRole.objects.get(
                infrastructure=infrastructure,
                user_id=user_id
            )
            return role_obj.role
        except InfrastructureUserRole.DoesNotExist:
            pass

        # Check invited_users M2M (populated for new invites via event consumer)
        if infrastructure.invited_users.filter(id=user_id).exists():
            return UserRole.USER

        # Fallback for existing invited users: check if user was invited by the infra owner
        from api.models.user import User
        try:
            user = User.objects.get(id=user_id)
            if user.invited_by and str(user.invited_by) == str(infrastructure.user_id):
                return UserRole.USER
        except User.DoesNotExist:
            pass

        return None
    
    @staticmethod
    def can_create_application(infrastructure, user_id):
        """Check if user can create applications (SUPER_ADMIN or ADMIN)"""
        role = InfrastructurePermissions.get_user_role(infrastructure, user_id)
        return role in [UserRole.SUPER_ADMIN, UserRole.ADMIN]
    
    @staticmethod
    def can_update_application(infrastructure, user_id):
        """Check if user can update applications (SUPER_ADMIN or ADMIN)"""
        role = InfrastructurePermissions.get_user_role(infrastructure, user_id)
        return role in [UserRole.SUPER_ADMIN, UserRole.ADMIN]
    
    @staticmethod
    def can_delete_application(infrastructure, user_id):
        """Check if user can delete applications (SUPER_ADMIN or ADMIN)"""
        role = InfrastructurePermissions.get_user_role(infrastructure, user_id)
        return role in [UserRole.SUPER_ADMIN, UserRole.ADMIN]
    
    @staticmethod
    def can_view_application(infrastructure, user_id):
        """Check if user can view applications (any role)"""
        role = InfrastructurePermissions.get_user_role(infrastructure, user_id)
        return role is not None
    
    @staticmethod
    def can_update_infrastructure(infrastructure, user_id):
        """Check if user can update infrastructure (SUPER_ADMIN only)"""
        role = InfrastructurePermissions.get_user_role(infrastructure, user_id)
        return role == UserRole.SUPER_ADMIN
    
    @staticmethod
    def can_delete_infrastructure(infrastructure, user_id):
        """Check if user can delete infrastructure (SUPER_ADMIN only)"""
        role = InfrastructurePermissions.get_user_role(infrastructure, user_id)
        return role == UserRole.SUPER_ADMIN
    
    @staticmethod
    def can_invite_users(infrastructure, user_id):
        """Check if user can invite others (SUPER_ADMIN only)"""
        role = InfrastructurePermissions.get_user_role(infrastructure, user_id)
        return role == UserRole.SUPER_ADMIN
