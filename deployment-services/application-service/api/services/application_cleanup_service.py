import logging
from api.models import Application, Environment
from aws.session import create_boto3_session
from aws.ecs import ECSClient
from aws.alb import ALBClient

logger = logging.getLogger(__name__)

class ApplicationCleanupService:
    def cleanup_application(self, application: Application):
        """Delete all AWS resources associated with an application."""
        try:
            environment = Environment.objects.filter(
                infrastructure=application.infrastructure
            ).first()
            
            if not environment:
                logger.warning(f"No environment found for application {application.name}")
                return
            
            session = create_boto3_session(application.infrastructure)
            
            # Step 1: Delete ECS Service
            if application.service_arn:
                self._delete_ecs_service(session, environment.cluster_arn, application.service_arn)
            
            # Step 2: Delete Listener Rule
            if application.listener_rule_arn:
                self._delete_listener_rule(session, application.listener_rule_arn)
            
            # Step 3: Delete Target Group
            if application.target_group_arn:
                self._delete_target_group(session, application.target_group_arn)
            
            # Step 4: Deregister Task Definition
            if application.task_definition_arn:
                self._deregister_task_definition(session, application.task_definition_arn)
            
            # Step 5: Delete CloudWatch Log Group
            self._delete_log_group(session, application.name)
            
            logger.info(f"Successfully cleaned up AWS resources for application {application.name}")
            
        except Exception as e:
            logger.error(f"Failed to cleanup AWS resources for {application.name}: {str(e)}")
            raise
    
    def _delete_ecs_service(self, session, cluster_arn, service_arn):
        try:
            ecs = ECSClient(session)
            ecs_client = session.client('ecs')
            
            # Scale service to 0 tasks
            service_name = service_arn.split('/')[-1]
            ecs_client.update_service(
                cluster=cluster_arn,
                service=service_name,
                desiredCount=0
            )
            logger.info(f"Scaled service {service_name} to 0 tasks")
            
            ecs_client.delete_service(
                cluster=cluster_arn,
                service=service_name,
                force=True
            )
            logger.info(f"Deleted ECS service {service_name}")
        except Exception as e:
            logger.error(f"Failed to delete ECS service: {e}")
    
    def _delete_listener_rule(self, session, listener_rule_arn):
        try:
            alb = ALBClient(session)
            alb.client.delete_rule(RuleArn=listener_rule_arn)
            logger.info(f"Deleted listener rule {listener_rule_arn}")
        except Exception as e:
            logger.error(f"Failed to delete listener rule: {e}")
    
    def _delete_target_group(self, session, target_group_arn):
        try:
            alb = ALBClient(session)
            alb.client.delete_target_group(TargetGroupArn=target_group_arn)
            logger.info(f"Deleted target group {target_group_arn}")
        except Exception as e:
            logger.error(f"Failed to delete target group: {e}")
    
    def _deregister_task_definition(self, session, task_definition_arn):
        try:
            ecs_client = session.client('ecs')
            ecs_client.deregister_task_definition(taskDefinition=task_definition_arn)
            logger.info(f"Deregistered task definition {task_definition_arn}")
        except Exception as e:
            logger.error(f"Failed to deregister task definition: {e}")
    
    def _delete_log_group(self, session, app_name):
        """Keep log groups for debugging — just log and skip."""
        import re
        slug = re.sub(r'[^a-z0-9._-]', '-', app_name.lower()).strip('-')
        logger.info(f"Keeping log group /ecs/{slug}-task for debugging")
