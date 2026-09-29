import logging

from aws.session import create_boto3_session

from api.models.application import Application
from api.models.environment import Environment
from api.repositories.infrastructure import InfrastructureRepository
from api.services.exit_enforcement import require_not_exited

logger = logging.getLogger(__name__)


class ApplicationSleepService:
    """Service for putting applications to sleep and waking them up."""

    def __init__(self):
        self.infra_repo = InfrastructureRepository()

    def _get_environment(self, infra):
        env = Environment.objects.filter(infrastructure=infra).first()
        if not env or not env.cluster_arn:
            raise ValueError("Infrastructure environment not found or cluster_arn missing")
        return env

    def _get_cluster_arn(self, infra):
        return self._get_environment(infra).cluster_arn

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
        require_not_exited(infra)

        cluster_arn = self._get_cluster_arn(infra)
        session = create_boto3_session(infra)
        ecs = session.client('ecs')

        try:
            response = ecs.describe_services(
                cluster=cluster_arn,
                services=[application.service_arn]
            )
            
            if not response['services']:
                raise ValueError("ECS service not found")
            
            current_count = response['services'][0]['desiredCount']
            
            ecs.update_service(
                cluster=cluster_arn,
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
        require_not_exited(infra)

        cluster_arn = self._get_cluster_arn(infra)
        session = create_boto3_session(infra)
        ecs = session.client('ecs')

        try:
            restore_count = application.desired_count if application.desired_count > 0 else 1

            ecs.update_service(
                cluster=cluster_arn,
                service=application.service_arn,
                desiredCount=restore_count
            )

            # Not yet serving traffic — DEPLOYING until complete_wake (below) confirms the
            # restored tasks are actually stable and healthy, then flips it to ACTIVE.
            application.is_sleeping = False
            application.status = 'DEPLOYING'
            application.desired_count = restore_count
            application.save(update_fields=['is_sleeping', 'status', 'desired_count'])

            logger.info(f"Application {application.name} wake requested (restoring count: {restore_count})")

            from api.services.deployment_queue import DeploymentQueue
            DeploymentQueue.enqueue_wake(application.id, application.infrastructure_id)

        except Exception as e:
            logger.error(f"Failed to wake application {application.name}: {e}")
            raise

    def complete_wake(self, application: Application):
        """Runs on the deployment worker queue, under the DeploymentLock for this app, after
        `wake_application` has already restored ECS desiredCount. Waits for the restored
        tasks to actually become stable and healthy, then marks the application ACTIVE —
        or FAILED with a sanitized message if they never do. `application.desired_count`
        was just set by `wake_application` to the exact count it told ECS to run.
        """
        from aws.alb import ALBClient
        from shared.errors.deploy_errors import sanitize_deploy_error

        from api.common.naming import app_slug as _slug
        from api.services.application_deployment_service import (
            ApplicationDeploymentService,
        )

        deploy_service = ApplicationDeploymentService()

        try:
            infra = self.infra_repo.get_infrastructure(application.infrastructure_id)
            if not infra:
                raise ValueError("Infrastructure not found")
            require_not_exited(infra)

            environment = self._get_environment(infra)
            session = deploy_service._create_aws_session(infra)

            service_name = f"{_slug(application.name)}-service"
            deploy_service._wait_for_service_stable_with_refresh(
                infra, environment.cluster_arn, service_name,
                expected_task_definition_arn=application.task_definition_arn,
            )

            alb = ALBClient(session)
            deploy_service._wait_for_target_healthy(
                alb, application.target_group_arn, desired_count=application.desired_count,
            )

            # H2: the wait above can run for minutes — re-check with a fresh read
            # immediately before the final write turns this app's stored status back into
            # "healthy". `infra` above is the same in-memory object fetched at the top of
            # this method, so re-checking it here would just repeat the same stale answer.
            deploy_service._abort_if_exited(application)
        except Exception as e:
            logger.error(f"Wake did not complete for application {application.name}: {e}")
            application.status = 'FAILED'
            application.error_message = sanitize_deploy_error(e)
            application.save(update_fields=['status', 'error_message'])
            return

        application.status = 'ACTIVE'
        application.error_message = None
        application.save(update_fields=['status', 'error_message'])
        logger.info(f"Application {application.name} woke up and is now ACTIVE")
