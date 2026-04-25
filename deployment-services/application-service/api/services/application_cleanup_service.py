import logging
import re
from api.models import Application, Environment
from aws.session import create_boto3_session
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
            ecs_client = session.client('ecs')
            service_name = service_arn.split('/')[-1]
            ecs_client.update_service(cluster=cluster_arn, service=service_name, desiredCount=0)
            logger.info(f"Scaled service {service_name} to 0 tasks")
            ecs_client.delete_service(cluster=cluster_arn, service=service_name, force=True)
            logger.info(f"Deleted ECS service {service_name}")

            import time
            svc = None
            for _ in range(30):
                resp = ecs_client.describe_services(cluster=cluster_arn, services=[service_name])
                svc = resp['services'][0] if resp['services'] else None
                if not svc or svc['status'] == 'INACTIVE':
                    return
                time.sleep(5)
            raise RuntimeError(
                f"ECS service {service_name} in cluster {cluster_arn} did not reach INACTIVE "
                f"after 150s — last status: {svc['status'] if svc else 'not found'}"
            )
        except Exception as e:
            logger.error(f"Failed to delete ECS service: {e}")
            raise

    def _delete_listener_rule(self, session, listener_rule_arn):
        try:
            alb = ALBClient(session)
            alb.client.delete_rule(RuleArn=listener_rule_arn)
            logger.info(f"Deleted listener rule {listener_rule_arn}")
        except Exception as e:
            logger.error(f"Failed to delete listener rule: {e}")

    def _delete_target_group(self, session, target_group_arn):
        import time
        alb = ALBClient(session)
        for attempt in range(6):
            try:
                alb.client.delete_target_group(TargetGroupArn=target_group_arn)
                logger.info(f"Deleted target group {target_group_arn}")
                return
            except alb.client.exceptions.ResourceInUseException:
                delay = min(5 * (2 ** attempt), 60)
                logger.warning(f"TG still in use, retrying in {delay}s (attempt {attempt + 1}/6)")
                time.sleep(delay)
            except Exception as e:
                logger.error(f"Failed to delete target group: {e}")
                raise
        raise RuntimeError(f"Target group {target_group_arn} still in use after 6 attempts — cleanup will be retried")
    
    def _deregister_task_definition(self, session, task_definition_arn):
        try:
            ecs_client = session.client('ecs')
            ecs_client.deregister_task_definition(taskDefinition=task_definition_arn)
            logger.info(f"Deregistered task definition {task_definition_arn}")
        except Exception as e:
            logger.error(f"Failed to deregister task definition: {e}")
    
    def _delete_log_group(self, session, app_name):
        """Keep log groups for debugging — just log and skip."""
        slug = re.sub(r'[^a-z0-9._-]', '-', app_name.lower()).strip('-')
        logger.info(f"Keeping log group /ecs/{slug}-task for debugging")
