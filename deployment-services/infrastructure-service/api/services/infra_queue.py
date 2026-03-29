import socket
import redis
import json
import logging
from django.conf import settings
from django.utils import timezone
from django.db import models
from datetime import timedelta

logger = logging.getLogger(__name__)

_BLPOP_TIMEOUT = 5

_pool = redis.ConnectionPool(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    db=settings.REDIS_DB,
    decode_responses=True,
    max_connections=20,
    socket_timeout=5,
    socket_connect_timeout=5,
    socket_keepalive=True,
    socket_keepalive_options={
        socket.TCP_KEEPIDLE: 60,
        socket.TCP_KEEPINTVL: 10,
        socket.TCP_KEEPCNT: 5,
    },
    retry_on_timeout=True,
    health_check_interval=30,
)

_blocking_pool = redis.ConnectionPool(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    db=settings.REDIS_DB,
    decode_responses=True,
    max_connections=5,
    socket_timeout=_BLPOP_TIMEOUT + 5,
    socket_connect_timeout=5,
    socket_keepalive=True,
    socket_keepalive_options={
        socket.TCP_KEEPIDLE: 60,
        socket.TCP_KEEPINTVL: 10,
        socket.TCP_KEEPCNT: 5,
    },
    health_check_interval=30,
)

def _redis():
    return redis.Redis(connection_pool=_pool)

PROVISION_QUEUE = "infra:provision"
DESTROY_QUEUE = "infra:destroy"
LOCK_PREFIX = "lock:infra:"
LOCK_TTL = 3600  # 1 hour


class InfraQueue:
    """Queue for infrastructure operations with deduplication"""
    
    @staticmethod
    def enqueue_provision(infra_id: str, priority: int = 0):
        """Add provision job to queue (deduplicated)"""
        lock_key = f"{LOCK_PREFIX}{infra_id}"
        if _redis().exists(lock_key):
            logger.warning(f"Job {infra_id} already queued or processing, skipping")
            return False
        
        job = {"infra_id": infra_id, "action": "provision", "priority": priority}
        _redis().rpush(PROVISION_QUEUE, json.dumps(job))
        _redis().setex(lock_key, LOCK_TTL, "queued")
        logger.info(f"Enqueued provision job for {infra_id}")
        return True
    
    @staticmethod
    def enqueue_destroy(infra_id: str):
        """Add destroy job to queue"""
        job = {"infra_id": infra_id, "action": "destroy"}
        _redis().rpush(DESTROY_QUEUE, json.dumps(job))
        logger.info(f"Enqueued destroy job for {infra_id}")
    
    @staticmethod
    def dequeue_provision(timeout: int = _BLPOP_TIMEOUT):
        """Get next provision job (blocking)"""
        result = redis.Redis(connection_pool=_blocking_pool).blpop(PROVISION_QUEUE, timeout=timeout)
        if result:
            _, job_data = result
            return json.loads(job_data)
        return None

    @staticmethod
    def dequeue_destroy(timeout: int = _BLPOP_TIMEOUT):
        """Get next destroy job. Uses non-blocking lpop when timeout=0."""
        if timeout == 0:
            result = _redis().lpop(DESTROY_QUEUE)
            if result:
                return json.loads(result)
            return None
        result = redis.Redis(connection_pool=_blocking_pool).blpop(DESTROY_QUEUE, timeout=timeout)
        if result:
            _, job_data = result
            return json.loads(job_data)
        return None
    
    @staticmethod
    def release_lock(infra_id: str):
        """Release job lock"""
        lock_key = f"{LOCK_PREFIX}{infra_id}"
        _redis().delete(lock_key)
        logger.info(f"Released lock for {infra_id}")
    
    @staticmethod
    def acquire_db_lock(infra_id: str, worker_id: str) -> bool:
        """Acquire application-level lock using conditional update (no DB row lock)."""
        from api.models.environment import Environment
        stale_threshold = timezone.now() - timedelta(hours=1)
        updated = Environment.objects.filter(
            infrastructure_id=infra_id
        ).filter(
            models.Q(locked_by__isnull=True) | models.Q(locked_at__lt=stale_threshold)
        ).update(locked_at=timezone.now(), locked_by=worker_id)
        if not updated:
            logger.warning(f"Infrastructure {infra_id} is already locked")
        return bool(updated)
    
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
