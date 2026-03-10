#!/usr/bin/env python
import os
import sys
import django
import logging
import time
import uuid

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from api.services.deployment_queue import DeploymentQueue
from api.services.application_deployment_service import ApplicationDeploymentService
from api.services.deployment_lock import DeploymentLock
from api.repositories.application import ApplicationRepository

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

WORKER_ID = str(uuid.uuid4())[:8]

def process_deployment(job):
    """Process a single deployment job"""
    app_id = job.get('app_id')
    logger.info(f"Worker {WORKER_ID} processing deployment for application {app_id}")
    
    lock = DeploymentLock()
    
    # Acquire distributed lock
    if not lock.acquire(app_id, WORKER_ID):
        logger.warning(f"Another worker is deploying app {app_id}, skipping")
        return
    
    try:
        app_repo = ApplicationRepository()
        app = app_repo.get_by_id(app_id)
        
        if not app:
            logger.error(f"Application {app_id} not found")
            return
        
        deployment_service = ApplicationDeploymentService()
        deployment_url = deployment_service.deploy_application(app)
        
        logger.info(f"Successfully deployed application {app_id} at {deployment_url}")
        
    except Exception as e:
        logger.error(f"Deployment failed for application {app_id}: {e}", exc_info=True)
    finally:
        lock.release(app_id, WORKER_ID)

def main():
    logger.info("Starting deployment worker...")
    
    while True:
        try:
            job = DeploymentQueue.dequeue_deployment()
            if job:
                process_deployment(job)
            else:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down deployment worker...")
            break
        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)
            time.sleep(5)

if __name__ == '__main__':
    main()
