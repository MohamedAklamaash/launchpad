import os
import uuid
import signal
import logging
from django.core.management.base import BaseCommand
from django.db import connections

os.environ['DB_CONN_MAX_AGE'] = '0'

logger = logging.getLogger(__name__)

def _close_db():
    for conn in connections.all():
        conn.close()


class Command(BaseCommand):
    help = 'Run the infrastructure provisioning worker'

    def handle(self, *args, **options):
        from api.services.infra_queue import InfraQueue
        from api.services.terraform_worker import TerraformWorker
        from api.services.notification import NotificationService
        from api.models.infrastructure import Infrastructure
        from api.models.environment import Environment

        worker_id = str(uuid.uuid4())[:8]
        running = True

        def _stop(sig, frame):
            nonlocal running
            logger.info(f"Worker {worker_id} shutting down...")
            running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        logger.info(f"Infrastructure worker {worker_id} started")

        # Re-enqueue any stuck pending/provisioning jobs on startup
        pending = Environment.objects.filter(status__in=['PENDING', 'PROVISIONING']).select_related('infrastructure')
        for env in pending:
            InfraQueue.release_lock(str(env.infrastructure_id))
            InfraQueue.enqueue_provision(str(env.infrastructure_id))
            logger.info(f"Re-enqueued {env.infrastructure_id}")

        while running:
            job = InfraQueue.dequeue_provision(timeout=1)
            if job:
                infra_id = job["infra_id"]
                if not InfraQueue.acquire_db_lock(infra_id, worker_id):
                    logger.warning(f"Could not acquire lock for {infra_id}, skipping")
                    continue
                try:
                    infra = Infrastructure.objects.get(id=infra_id)
                    TerraformWorker.provision(infra_id)
                    env = Environment.objects.get(infrastructure_id=infra_id)
                    if env.status == "ACTIVE":
                        NotificationService.send_provision_success(str(infra.user_id), infra_id, infra.name)
                    elif env.status == "ERROR":
                        NotificationService.send_provision_failure(str(infra.user_id), infra_id, infra.name, env.error_message or "Unknown error")
                except Exception as e:
                    logger.error(f"Provision failed for {infra_id}: {e}", exc_info=True)
                    try:
                        infra = Infrastructure.objects.get(id=infra_id)
                        NotificationService.send_provision_failure(str(infra.user_id), infra_id, infra.name, str(e))
                    except Exception:
                        pass
                finally:
                    InfraQueue.release_db_lock(infra_id)
                    InfraQueue.release_lock(infra_id)
                    _close_db()

            job = InfraQueue.dequeue_destroy(timeout=1)
            if job:
                infra_id = job["infra_id"]
                try:
                    infra = Infrastructure.objects.get(id=infra_id)
                    TerraformWorker.destroy(infra_id)
                    try:
                        env = Environment.objects.get(infrastructure_id=infra_id)
                        if env.status == "DESTROYED":
                            NotificationService.send_destroy_success(str(infra.user_id), infra_id, infra.name)
                            # Terraform destroy succeeded — now delete the DB records
                            env.delete()
                            infra.delete()
                            logger.info(f"Deleted DB records for infrastructure {infra_id}")
                        else:
                            NotificationService.send_destroy_failure(str(infra.user_id), infra_id, infra.name, env.error_message or "Unknown error")
                    except Environment.DoesNotExist:
                        NotificationService.send_destroy_success(str(infra.user_id), infra_id, infra.name)
                        infra.delete()
                except Infrastructure.DoesNotExist:
                    logger.warning(f"Infrastructure {infra_id} already deleted")
                except Exception as e:
                    logger.error(f"Destroy failed for {infra_id}: {e}", exc_info=True)
                    try:
                        infra = Infrastructure.objects.get(id=infra_id)
                        NotificationService.send_destroy_failure(str(infra.user_id), infra_id, infra.name, str(e))
                    except Exception:
                        pass
                finally:
                    _close_db()

        logger.info(f"Worker {worker_id} stopped")
