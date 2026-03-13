from shared.enums.user_role import UserRole
import logging

logger = logging.getLogger(__name__)


class InfrastructurePermissions:
    """Check user permissions for infrastructure operations (infrastructure-service)"""
    
    @staticmethod
    def is_super_admin(infrastructure, user_id):
        """Check if user is the infrastructure owner (SUPER_ADMIN)"""
        return str(infrastructure.user_id) == str(user_id)
    
    @staticmethod
    def can_update_infrastructure(infrastructure, user_id):
        """Only SUPER_ADMIN (owner) can update infrastructure"""
        return InfrastructurePermissions.is_super_admin(infrastructure, user_id)
    
    @staticmethod
    def can_delete_infrastructure(infrastructure, user_id):
        """Only SUPER_ADMIN (owner) can delete infrastructure"""
        return InfrastructurePermissions.is_super_admin(infrastructure, user_id)
