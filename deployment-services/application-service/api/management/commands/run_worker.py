import os
import time
import uuid
import logging
import threading
import queue
from concurrent.futures import ThreadPoolExecutor
from django.core.management.base import BaseCommand
from django.db import connections

os.environ['DB_CONN_MAX_AGE'] = '0'

logger = logging.getLogger(__name__)

MAX_INFRA_WORKERS = int(os.environ.get('DEPLOYMENT_MAX_INFRA_WORKERS', '10'))


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
        logger.info(f"Starting deployment worker {worker_id} (max {MAX_INFRA_WORKERS} parallel infrastructures)")

        # Recover any jobs that were in-flight when the previous worker crashed.
        # Fail startup if recovery fails — don't run with unknown queue state.
        try:
            recovered = DeploymentQueue.recover_processing_jobs()
            logger.info(f"Startup: recovered {recovered} in-flight job(s) from processing queue")
        except Exception as e:
            logger.critical(f"CRITICAL: Failed to recover processing jobs on startup: {e}")
            raise

        # Each infra gets its own queue + a single dedicated thread draining it.
        infra_queues: dict[str, queue.Queue] = {}
        infra_queues_lock = threading.Lock()
        executor = ThreadPoolExecutor(max_workers=MAX_INFRA_WORKERS, thread_name_prefix='infra-worker')

        def run_deploy(app_id, job):
            lock = DeploymentLock()
            if not lock.acquire(app_id, worker_id):
                logger.warning(f"App {app_id} already locked, skipping")
                DeploymentQueue.ack_job(job)
                return
            try:
                app = ApplicationRepository().get_by_id(app_id)
                if not app:
                    logger.error(f"Application {app_id} not found")
                    DeploymentQueue.ack_job(job)
                    return
                url = ApplicationDeploymentService().deploy_application(app)
                logger.info(f"Deployed {app_id} at {url}")
                DeploymentQueue.ack_job(job)
            except Exception as e:
                logger.error(f"Deployment failed for {app_id}: {e}", exc_info=True)
                DeploymentQueue.nack_job(job)
            finally:
                lock.release(app_id, worker_id)
                _close_db()

        def run_cleanup(job):
            from api.models.infrastructure import Infrastructure
            from aws.session import create_boto3_session
            app_id = job.get('app_id')
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
                DeploymentQueue.ack_job(job)
            except Exception as e:
                logger.error(f"AWS cleanup failed for app {app_id}: {e}", exc_info=True)
                DeploymentQueue.nack_job(job)
            finally:
                _close_db()

        def drain_infra_queue(infra_id: str, q: queue.Queue):
            """Drain all jobs for one infrastructure serially, then remove the queue."""
            logger.info(f"Infra worker started for {infra_id}")
            while True:
                try:
                    job = q.get(timeout=30)
                except queue.Empty:
                    with infra_queues_lock:
                        if q.empty():
                            del infra_queues[infra_id]
                            logger.info(f"Infra worker exiting for {infra_id}")
                            return
                        # Jobs arrived between the empty check and the lock — keep draining
                    continue

                try:
                    if job.get('action') == 'deploy':
                        run_deploy(job['app_id'], job)
                except Exception as e:
                    logger.error(f"Unhandled error in drain loop for {infra_id}: {e}", exc_info=True)
                    DeploymentQueue.nack_job(job)
                finally:
                    q.task_done()

        def dispatch(job):
            action = job.get('action', 'deploy')

            if action == 'cleanup':
                executor.submit(run_cleanup, job)
                return

            infra_id = job.get('infrastructure_id')
            if not infra_id:
                try:
                    app = ApplicationRepository().get_by_id(job['app_id'])
                    infra_id = str(app.infrastructure_id) if app else None
                    _close_db()
                except Exception:
                    infra_id = None

            if not infra_id:
                executor.submit(run_deploy, job['app_id'], job)
                return

            with infra_queues_lock:
                if infra_id not in infra_queues:
                    q = queue.Queue()
                    infra_queues[infra_id] = q
                    q.put(job)
                    executor.submit(drain_infra_queue, infra_id, q)
                    logger.info(f"Spawned new infra worker for {infra_id}")
                else:
                    infra_queues[infra_id].put(job)
                    logger.info(f"Queued job for existing infra worker {infra_id}")

        while True:
            try:
                job = DeploymentQueue.dequeue_deployment()
                if job:
                    dispatch(job)
                else:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Worker shutting down")
                executor.shutdown(wait=True)
                break
            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)
                _close_db()
                time.sleep(5)
