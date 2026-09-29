import logging

from aws.alb import ALBClient
from aws.session import create_boto3_session

from api.common.naming import ecs_log_group_for
from api.k8s.deployer import delete_runtime_resources
from api.models import Application, Environment

logger = logging.getLogger(__name__)

# Above the ALB target group's default 300s deregistration delay, which bounds DRAINING.
ECS_SERVICE_INACTIVE_TIMEOUT_SECONDS = 420

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

            if application.runtime_refs:
                delete_runtime_resources(
                    session, application.infrastructure, environment, application.runtime_refs
                )
                logger.info(f"Successfully cleaned up Kubernetes resources for {application.name}")
                return

            # Step 1: Delete ECS Service
            if application.service_arn:
                self._delete_ecs_service(session, environment.cluster_arn, application.service_arn)
            
            # Step 2: Delete Listener Rule
            if application.listener_rule_arn:
                self._delete_listener_rule(session, application.listener_rule_arn)

            # Step 2.5: Delete the host-mode 443 forward rule (F1b part 3a), if this app
            # was ever deployed in host mode. The per-infra :80 wildcard redirect rule
            # (aws/alb.py:ensure_host_redirect_rule) is intentionally NOT deleted here — it
            # is shared by every app on this infra, not owned by this one, and is torn
            # down with the ALB itself at infra teardown.
            if application.host_forward_rule_arn:
                self._delete_listener_rule(session, application.host_forward_rule_arn)

            # Step 3: Delete Target Group
            if application.target_group_arn:
                self._delete_target_group(session, application.target_group_arn)
            
            # Step 4: Deregister Task Definition
            if application.task_definition_arn:
                self._deregister_task_definition(session, application.task_definition_arn)
            
            # Step 5: Delete CloudWatch Log Group
            self._delete_log_group(session, application)
            
            logger.info(f"Successfully cleaned up AWS resources for application {application.name}")
            
        except Exception as e:
            logger.error(f"Failed to cleanup AWS resources for {application.name}: {e!s}")
            raise
    
    def _delete_ecs_service(self, session, cluster_arn, service_arn):
        """Idempotent: a retry must finish a delete an earlier attempt started. Seen on real
        AWS: attempt 1 deleted the service and timed out waiting for INACTIVE; attempt 2 then
        called update_service on the DRAINING service, got ServiceNotActiveException, and
        the cleanup dead-lettered with the listener rules and target group still in place."""
        import time

        ecs_client = session.client('ecs')
        service_name = service_arn.split('/')[-1]

        def _status():
            resp = ecs_client.describe_services(cluster=cluster_arn, services=[service_name])
            return resp['services'][0]['status'] if resp['services'] else None

        try:
            status = _status()
            if status in (None, 'INACTIVE'):
                logger.info(f"ECS service {service_name} already gone")
                return
            if status == 'ACTIVE':
                try:
                    ecs_client.update_service(cluster=cluster_arn, service=service_name, desiredCount=0)
                    logger.info(f"Scaled service {service_name} to 0 tasks")
                    ecs_client.delete_service(cluster=cluster_arn, service=service_name, force=True)
                    logger.info(f"Deleted ECS service {service_name}")
                except ecs_client.exceptions.ServiceNotActiveException:
                    pass  # another attempt got there first; wait for it below
                except ecs_client.exceptions.ServiceNotFoundException:
                    return

            # DRAINING lasts at least the target group's deregistration delay (ALB default
            # 300s), so the wait must outlast it — the previous 150s always timed out for a
            # service that still had registered tasks.
            deadline = time.monotonic() + ECS_SERVICE_INACTIVE_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                status = _status()
                if status in (None, 'INACTIVE'):
                    return
                time.sleep(10)
            raise RuntimeError(
                f"ECS service {service_name} in cluster {cluster_arn} did not reach INACTIVE "
                f"after {ECS_SERVICE_INACTIVE_TIMEOUT_SECONDS}s — last status: {status}"
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
            return
        # A deregistered revision is inactive but still visible/billed-for-storage until
        # actually deleted. No infra-scoped family prefix is safe to list-and-sweep by
        # (a legacy family is shared by every app with this slug across infras — see
        # api/common/naming.py's ecs_log_group/ecs_task_family docstrings), so this ARN
        # — captured while the row still exists — is the only safe way to remove it.
        try:
            ecs_client.delete_task_definitions(taskDefinitions=[task_definition_arn])
            logger.info(f"Deleted task definition revision {task_definition_arn}")
        except Exception as e:
            logger.warning(f"Failed to delete task definition revision {task_definition_arn}: {e}")
    
    def _delete_log_group(self, session, application):
        """Keep log groups for debugging — just log and skip. H4: reads the app's own
        stored log group name (falling back to the legacy shared name for a row that
        never got one), not a re-derived slug-only name — otherwise this would log the
        wrong group for any app deployed under the new per-infra+per-app hashed name."""
        logger.info(f"Keeping log group {ecs_log_group_for(application)} for debugging")
