import os
import time
import uuid
import logging
from django.core.management.base import BaseCommand
from django.db import connections

os.environ['DB_CONN_MAX_AGE'] = '0'

logger = logging.getLogger(__name__)


def _close_db():
    for conn in connections.all():
        conn.close()


class Command(BaseCommand):
    help = 'Run the deployment worker'

    def handle(self, *args, **options):
        from api.services.deployment_queue import DeploymentQueue
        from api.services.application_deployment_service import ApplicationDeploymentService
        from api.services.application_cleanup_service import ApplicationCleanupService
        from api.services.deployment_lock import DeploymentLock
        from api.repositories.application import ApplicationRepository

        worker_id = str(uuid.uuid4())[:8]
        logger.info(f"Starting deployment worker {worker_id}")

        def process(job):
            action = job.get('action', 'deploy')
            app_id = job.get('app_id')

            if action == 'cleanup':
                from api.models.infrastructure import Infrastructure
                from aws.session import create_boto3_session
                try:
                    infra = Infrastructure.objects.get(id=job['infrastructure_id'])
                    session = create_boto3_session(infra)
                    cleanup = ApplicationCleanupService()
                    if job.get('service_arn'):
                        env = infra.environments.first()
                        cleanup._delete_ecs_service(session, env.cluster_arn if env else None, job['service_arn'])
                    if job.get('listener_rule_arn'):
                        cleanup._delete_listener_rule(session, job['listener_rule_arn'])
                    if job.get('target_group_arn'):
                        cleanup._delete_target_group(session, job['target_group_arn'])
                    if job.get('task_definition_arn'):
                        cleanup._deregister_task_definition(session, job['task_definition_arn'])
                    logger.info(f"Cleaned up AWS resources for deleted app {app_id}")
                except Exception as e:
                    logger.error(f"AWS cleanup failed for app {app_id}: {e}", exc_info=True)
                finally:
                    _close_db()
                return

            lock = DeploymentLock()
            if not lock.acquire(app_id, worker_id):
                logger.warning(f"App {app_id} already locked, skipping")
                return
            try:
                app = ApplicationRepository().get_by_id(app_id)
                if not app:
                    logger.error(f"Application {app_id} not found")
                    return
                url = ApplicationDeploymentService().deploy_application(app)
                logger.info(f"Deployed {app_id} at {url}")
            except Exception as e:
                logger.error(f"Deployment failed for {app_id}: {e}", exc_info=True)
            finally:
                lock.release(app_id, worker_id)
                _close_db()

        while True:
            try:
                job = DeploymentQueue.dequeue_deployment()
                if job:
                    process(job)
                else:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Worker shutting down")
                break
            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)
                _close_db()
                time.sleep(5)
