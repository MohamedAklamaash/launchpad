import redis
import json
import logging
import uuid
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    db=settings.REDIS_DB,
    decode_responses=True
)

PROVISION_QUEUE = "infra:provision"
DESTROY_QUEUE = "infra:destroy"
LOCK_PREFIX = "lock:infra:"
LOCK_TTL = 3600  # 1 hour


class InfraQueue:
    """Queue for infrastructure operations with deduplication"""
    
    @staticmethod
    def enqueue_provision(infra_id: str, priority: int = 0):
        """Add provision job to queue (deduplicated)"""
        # Check if already in queue
        lock_key = f"{LOCK_PREFIX}{infra_id}"
        if redis_client.exists(lock_key):
            logger.warning(f"Job {infra_id} already queued or processing, skipping")
            return False
        
        job = {"infra_id": infra_id, "action": "provision", "priority": priority}
        redis_client.rpush(PROVISION_QUEUE, json.dumps(job))
        redis_client.setex(lock_key, LOCK_TTL, "queued")
        logger.info(f"Enqueued provision job for {infra_id}")
        return True
    
    @staticmethod
    def enqueue_destroy(infra_id: str):
        """Add destroy job to queue"""
        job = {"infra_id": infra_id, "action": "destroy"}
        redis_client.rpush(DESTROY_QUEUE, json.dumps(job))
        logger.info(f"Enqueued destroy job for {infra_id}")
    
    @staticmethod
    def dequeue_provision(timeout: int = 5):
        """Get next provision job (blocking)"""
        result = redis_client.blpop(PROVISION_QUEUE, timeout=timeout)
        if result:
            _, job_data = result
            return json.loads(job_data)
        return None
    
    @staticmethod
    def dequeue_destroy(timeout: int = 5):
        """Get next destroy job (blocking)"""
        result = redis_client.blpop(DESTROY_QUEUE, timeout=timeout)
        if result:
            _, job_data = result
            return json.loads(job_data)
        return None
    
    @staticmethod
    def release_lock(infra_id: str):
        """Release job lock"""
        lock_key = f"{LOCK_PREFIX}{infra_id}"
        redis_client.delete(lock_key)
        logger.info(f"Released lock for {infra_id}")
    
    @staticmethod
    def acquire_db_lock(infra_id: str, worker_id: str) -> bool:
        """Acquire database-level lock for infrastructure"""
        from api.models.environment import Environment
        from django.db import transaction
        
        try:
            with transaction.atomic():
                env = Environment.objects.select_for_update(nowait=True).get(
                    infrastructure_id=infra_id
                )
                
                # Check if already locked
                if env.locked_at and env.locked_by:
                    lock_age = timezone.now() - env.locked_at
                    if lock_age < timedelta(hours=1):
                        logger.warning(f"Infrastructure {infra_id} locked by {env.locked_by}")
                        return False
                
                # Acquire lock
                env.locked_at = timezone.now()
                env.locked_by = worker_id
                env.save(update_fields=['locked_at', 'locked_by'])
                return True
        except Exception as e:
            logger.error(f"Failed to acquire lock for {infra_id}: {e}")
            return False
    
    @staticmethod
    def release_db_lock(infra_id: str):
        """Release database-level lock"""
        from api.models.environment import Environment
        try:
            Environment.objects.filter(infrastructure_id=infra_id).update(
                locked_at=None,
                locked_by=None
            )
        except Exception as e:
            logger.error(f"Failed to release DB lock for {infra_id}: {e}")
