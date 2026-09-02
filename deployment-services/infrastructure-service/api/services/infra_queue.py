import json
import logging
import os
import socket
from datetime import timedelta

import redis
from django.conf import settings
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

# A DB lock is considered abandoned once it hasn't been refreshed for this long. The provisioning
# worker heartbeats the lock every INFRA_LOCK_HEARTBEAT_SECONDS while a job is dispatched, so a
# live job (running or queued in the executor) never crosses this window — only a crashed worker's
# lock goes stale. Keep it well above the heartbeat interval so a transient DB blip can't strand a
# live job. The reaper re-enqueues on the same window (see run_worker.STUCK_THRESHOLD).
DB_LOCK_STALENESS_SECONDS = int(os.environ.get("INFRA_DB_LOCK_STALENESS_SECONDS", "900"))

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
        """Add destroy job to queue (deduplicated on the shared per-infra lock, so a
        double-click or event replay can't spawn concurrent terraform destroys)."""
        lock_key = f"{LOCK_PREFIX}{infra_id}"
        if _redis().exists(lock_key):
            logger.warning(f"Destroy job {infra_id} already queued or processing, skipping")
            return False
        job = {"infra_id": infra_id, "action": "destroy"}
        _redis().rpush(DESTROY_QUEUE, json.dumps(job))
        _redis().setex(lock_key, LOCK_TTL, "queued")
        logger.info(f"Enqueued destroy job for {infra_id}")
        return True
    
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
    def has_lock(infra_id: str) -> bool:
        """Whether the Redis dedup key is still set — i.e. the job is enqueued or in flight.
        The reaper uses this as the authoritative 'is it lost' signal for a row with no DB lock,
        instead of guessing from timestamps."""
        return bool(_redis().exists(f"{LOCK_PREFIX}{infra_id}"))
    
    @staticmethod
    def acquire_db_lock(infra_id: str, worker_id: str) -> bool:
        """Acquire application-level lock using conditional update (no DB row lock)."""
        from api.models.environment import Environment
        stale_threshold = timezone.now() - timedelta(seconds=DB_LOCK_STALENESS_SECONDS)
        updated = Environment.objects.filter(
            infrastructure_id=infra_id
        ).filter(
            models.Q(locked_by__isnull=True) | models.Q(locked_at__lt=stale_threshold)
        ).update(locked_at=timezone.now(), locked_by=worker_id)
        if not updated:
            logger.warning(f"Infrastructure {infra_id} is already locked")
        return bool(updated)

    @staticmethod
    def refresh_db_lock(infra_id: str, worker_id: str) -> bool:
        """Heartbeat: push the lock's freshness forward, but only while this worker still owns it.
        Owner-scoping means a refresh that races a handover (another worker stole a stale lock)
        matches zero rows and no-ops instead of clobbering the new owner."""
        from api.models.environment import Environment
        updated = Environment.objects.filter(
            infrastructure_id=infra_id, locked_by=worker_id
        ).update(locked_at=timezone.now())
        return bool(updated)

    @staticmethod
    def release_db_lock(infra_id: str, worker_id: str | None = None):
        """Release the DB lock. With worker_id, release only if this worker still holds it, so a
        job that overran the staleness window and was handed off can't wipe the new owner's lock.
        Without worker_id (startup/reaper cleanup of a known-dead worker), release unconditionally."""
        from api.models.environment import Environment
        try:
            qs = Environment.objects.filter(infrastructure_id=infra_id)
            if worker_id is not None:
                qs = qs.filter(locked_by=worker_id)
            qs.update(locked_at=None, locked_by=None)
        except Exception as e:
            logger.error(f"Failed to release DB lock for {infra_id}: {e}")

    @staticmethod
    def bump_reap_count(infra_id: str) -> int:
        """Count how many times the reaper has re-driven this infra, so a job that keeps hard-
        crashing the worker mid-run can be parked in ERROR instead of re-running terraform against
        the customer account forever. Cleared on a clean finish."""
        key = f"reap:count:{infra_id}"
        count = _redis().incr(key)
        _redis().expire(key, DB_LOCK_STALENESS_SECONDS * 4)
        return int(count)

    @staticmethod
    def clear_reap_count(infra_id: str):
        _redis().delete(f"reap:count:{infra_id}")
