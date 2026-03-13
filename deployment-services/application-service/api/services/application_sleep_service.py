import logging
from api.models.application import Application
from api.repositories.infrastructure import InfrastructureRepository
from aws.session import create_boto3_session
import boto3

logger = logging.getLogger(__name__)


class ApplicationSleepService:
    """Service for putting applications to sleep and waking them up."""
    
    def __init__(self):
        self.infra_repo = InfrastructureRepository()
    
    def sleep_application(self, application: Application):
        """Put application to sleep by scaling ECS service to 0 tasks."""
        if application.status != 'ACTIVE':
            raise ValueError(f"Cannot sleep application in {application.status} state")
        
        if application.is_sleeping:
            raise ValueError("Application is already sleeping")
        
        if not application.service_arn:
            raise ValueError("Application has no ECS service")
        
        infra = self.infra_repo.get_infrastructure(application.infrastructure_id)
        if not infra:
            raise ValueError("Infrastructure not found")
        
        session = create_boto3_session(infra)
        ecs = session.client('ecs')
        
        try:
            response = ecs.describe_services(
                cluster=infra.metadata['cluster_arn'],
                services=[application.service_arn]
            )
            
            if not response['services']:
                raise ValueError("ECS service not found")
            
            current_count = response['services'][0]['desiredCount']
            
            ecs.update_service(
                cluster=infra.metadata['cluster_arn'],
                service=application.service_arn,
                desiredCount=0
            )
            
            application.is_sleeping = True
            application.desired_count = current_count
            application.status = 'SLEEPING'
            application.save(update_fields=['is_sleeping', 'desired_count', 'status'])
            
            logger.info(f"Application {application.name} put to sleep (saved count: {current_count})")
            
        except Exception as e:
            logger.error(f"Failed to sleep application {application.name}: {e}")
            raise
    
    def wake_application(self, application: Application):
        """Wake application by restoring ECS service desired count."""
        if not application.is_sleeping:
            raise ValueError("Application is not sleeping")
        
        if not application.service_arn:
            raise ValueError("Application has no ECS service")
        
        infra = self.infra_repo.get_infrastructure(application.infrastructure_id)
        if not infra:
            raise ValueError("Infrastructure not found")
        
        session = create_boto3_session(infra)
        ecs = session.client('ecs')
        
        try:
            restore_count = application.desired_count if application.desired_count > 0 else 1
            
            ecs.update_service(
                cluster=infra.metadata['cluster_arn'],
                service=application.service_arn,
                desiredCount=restore_count
            )
            
            application.is_sleeping = False
            application.status = 'ACTIVE'
            application.save(update_fields=['is_sleeping', 'status'])
            
            logger.info(f"Application {application.name} woken up (restored count: {restore_count})")
            
        except Exception as e:
            logger.error(f"Failed to wake application {application.name}: {e}")
            raise
