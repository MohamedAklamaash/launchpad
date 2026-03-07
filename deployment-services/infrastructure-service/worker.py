#!/usr/bin/env python
import os
import sys
import django
import logging
import signal
import uuid

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from api.services.infra_queue import InfraQueue
from api.services.terraform_worker import TerraformWorker
from api.services.notification import NotificationService
from api.models.infrastructure import Infrastructure

logger = logging.getLogger(__name__)

running = True
WORKER_ID = str(uuid.uuid4())[:8]

def signal_handler(sig, frame):
    global running
    logger.info(f"Worker {WORKER_ID} shutting down...")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def main():
    logger.info(f"Infrastructure worker {WORKER_ID} started")
    
    # Recover pending jobs on startup
    from api.models.environment import Environment
    pending_envs = Environment.objects.filter(status__in=['PENDING', 'PROVISIONING']).select_related('infrastructure')
    
    if pending_envs.exists():
        logger.info(f"Found {pending_envs.count()} pending/provisioning infrastructures, re-enqueueing...")
        for env in pending_envs:
            # Clear any stale locks
            InfraQueue.release_lock(str(env.infrastructure_id))
            # Re-enqueue
            if InfraQueue.enqueue_provision(str(env.infrastructure_id)):
                logger.info(f"Re-enqueued infrastructure {env.infrastructure_id}")
            else:
                logger.warning(f"Infrastructure {env.infrastructure_id} already in queue")
    
    while running:
        # Check provision queue
        job = InfraQueue.dequeue_provision(timeout=1)
        if job:
            infra_id = job["infra_id"]
            logger.info(f"Worker {WORKER_ID} processing provision job: {infra_id}")
            
            # Acquire lock
            if not InfraQueue.acquire_db_lock(infra_id, WORKER_ID):
                logger.warning(f"Could not acquire lock for {infra_id}, skipping")
                InfraQueue.release_lock(infra_id)
                continue
            
            try:
                infra = Infrastructure.objects.get(id=infra_id)
                TerraformWorker.provision(infra_id)
                
                # Check final status
                from api.models.environment import Environment
                env = Environment.objects.get(infrastructure_id=infra_id)
                
                if env.status == "ACTIVE":
                    NotificationService.send_provision_success(
                        str(infra.user_id), 
                        infra_id, 
                        infra.name
                    )
                elif env.status == "ERROR":
                    NotificationService.send_provision_failure(
                        str(infra.user_id),
                        infra_id,
                        infra.name,
                        env.error_message or "Unknown error"
                    )
            except Exception as e:
                logger.error(f"Worker failed to provision {infra_id}: {str(e)}", exc_info=True)
                try:
                    infra = Infrastructure.objects.get(id=infra_id)
                    NotificationService.send_provision_failure(
                        str(infra.user_id),
                        infra_id,
                        infra.name,
                        str(e)
                    )
                except:
                    pass
            finally:
                InfraQueue.release_db_lock(infra_id)
                InfraQueue.release_lock(infra_id)
        
        # Check destroy queue
        job = InfraQueue.dequeue_destroy(timeout=1)
        if job:
            infra_id = job["infra_id"]
            logger.info(f"Worker {WORKER_ID} processing destroy job: {infra_id}")
            
            try:
                infra = Infrastructure.objects.get(id=infra_id)
                TerraformWorker.destroy(infra_id)
                
                from api.models.environment import Environment
                env = Environment.objects.get(infrastructure_id=infra_id)
                
                if env.status == "DESTROYED":
                    NotificationService.send_destroy_success(
                        str(infra.user_id),
                        infra_id,
                        infra.name
                    )
                else:
                    NotificationService.send_destroy_failure(
                        str(infra.user_id),
                        infra_id,
                        infra.name,
                        env.error_message or "Unknown error"
                    )
            except Exception as e:
                logger.error(f"Worker failed to destroy {infra_id}: {str(e)}", exc_info=True)
                try:
                    infra = Infrastructure.objects.get(id=infra_id)
                    NotificationService.send_destroy_failure(
                        str(infra.user_id),
                        infra_id,
                        infra.name,
                        str(e)
                    )
                except:
                    pass
    
    logger.info(f"Worker {WORKER_ID} stopped")


if __name__ == "__main__":
    main()
