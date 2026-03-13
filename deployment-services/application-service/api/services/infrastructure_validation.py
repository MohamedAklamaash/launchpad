from api.models.application import Application
import logging

logger = logging.getLogger(__name__)


class InfrastructureValidation:
    """Validation logic for infrastructure operations"""
    
    @staticmethod
    def can_delete_infrastructure(infra_id):
        """Check if infrastructure can be deleted (no apps exist)"""
        app_count = Application.objects.filter(infrastructure_id=infra_id).count()
        
        if app_count > 0:
            return False, f"Cannot delete infrastructure. {app_count} application(s) still exist. Delete all applications first."
        
        return True, None
    
    @staticmethod
    def get_infrastructure_apps_count(infra_id):
        """Get count of applications in infrastructure"""
        return Application.objects.filter(infrastructure_id=infra_id).count()
