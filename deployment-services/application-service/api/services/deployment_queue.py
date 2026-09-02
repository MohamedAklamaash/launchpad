import json
import logging
import socket

import redis

from api.common.envs.application import app_config

logger = logging.getLogger(__name__)

_BLPOP_TIMEOUT = 5

_pool = redis.ConnectionPool(
    host=app_config.redis_host,
    port=app_config.redis_port,
    password=app_config.redis_password,
    db=app_config.redis_db,
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
    host=app_config.redis_host,
    port=app_config.redis_port,
    password=app_config.redis_password,
    db=app_config.redis_db,
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


class DeploymentQueue:
    QUEUE_NAME = "deployment_queue"
    PROCESSING_QUEUE = "deployment_queue:processing"
    DLQ_NAME = "deployment_queue:dlq"
    MAX_RETRIES = 3

    @staticmethod
    def get_redis() -> redis.Redis:
        return redis.Redis(connection_pool=_pool)

    @staticmethod
    def enqueue_deployment(app_id: str, infrastructure_id: str | None = None):
        try:
            job = {"app_id": str(app_id), "action": "deploy", "retry_count": 0}
            if infrastructure_id:
                job["infrastructure_id"] = str(infrastructure_id)
            DeploymentQueue.get_redis().rpush(DeploymentQueue.QUEUE_NAME, json.dumps(job))
            logger.info(f"Enqueued deployment for application {app_id}")
        except Exception as e:
            logger.error(f"Failed to enqueue deployment: {e}")
            raise

    @staticmethod
    def enqueue_cleanup(app_id: str, infrastructure_id: str, service_arn: str | None = None,
                        listener_rule_arn: str | None = None, target_group_arn: str | None = None,
                        task_definition_arn: str | None = None):
        try:
            job = {
                "app_id": str(app_id),
                "action": "cleanup",
                "infrastructure_id": str(infrastructure_id),
                "service_arn": service_arn,
                "listener_rule_arn": listener_rule_arn,
                "target_group_arn": target_group_arn,
                "task_definition_arn": task_definition_arn,
                "retry_count": 0,
            }
            DeploymentQueue.get_redis().rpush(DeploymentQueue.QUEUE_NAME, json.dumps(job))
            logger.info(f"Enqueued cleanup for application {app_id}")
        except Exception as e:
            logger.error(f"Failed to enqueue cleanup: {e}")
            raise

    @staticmethod
    def recover_processing_jobs() -> int:
        """On worker startup, move any jobs stuck in the processing queue back to main queue.
        Returns the number of recovered jobs. Raises on Redis failure — caller should abort startup.
        """
        r = DeploymentQueue.get_redis()
        recovered = 0
        while True:
            job_data = r.rpoplpush(DeploymentQueue.PROCESSING_QUEUE, DeploymentQueue.QUEUE_NAME)
            if not job_data:
                break
            recovered += 1
        if recovered:
            logger.warning(f"Recovered {recovered} in-flight job(s) from processing queue")
        return recovered

    @staticmethod
    def reap_orphaned_processing_jobs(is_claimed=None) -> int:
        """Re-queue processing jobs whose app has no active deploy lock (worker died mid-deploy,
        or couldn't acquire the lock). Unlike recover_processing_jobs (startup-only, unconditional),
        this runs periodically so a long-lived worker no longer strands jobs until restart.

        A job is left alone if it's actively deploying (lock held) or `is_claimed(app_id)` — the
        latter covers jobs sitting in a live worker's in-memory infra queue that haven't acquired
        the lock yet, which would otherwise be falsely reaped into a duplicate deploy."""
        from api.services.deployment_lock import DeploymentLock
        r = DeploymentQueue.get_redis()
        lock = DeploymentLock()
        reaped = 0
        for raw in r.lrange(DeploymentQueue.PROCESSING_QUEUE, 0, -1):
            try:
                job = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            app_id = job.get('app_id')
            if app_id and (lock.is_locked(app_id) or (is_claimed and is_claimed(app_id))):
                continue  # a live worker owns it — leave it alone
            # Orphaned: push back to the main queue first (crash-safe), then drop the processing
            # copy. If another party already removed it, undo the push so we don't duplicate.
            r.rpush(DeploymentQueue.QUEUE_NAME, raw)
            if r.lrem(DeploymentQueue.PROCESSING_QUEUE, 1, raw):
                reaped += 1
            else:
                r.lrem(DeploymentQueue.QUEUE_NAME, 1, raw)
        if reaped:
            logger.warning(f"Reaped {reaped} orphaned job(s) from processing queue")
        return reaped

    @staticmethod
    def dequeue_deployment():
        try:
            r = redis.Redis(connection_pool=_blocking_pool)
            # Atomically move from main queue → processing queue (crash-safe)
            job_data = r.blmove(
                DeploymentQueue.QUEUE_NAME,
                DeploymentQueue.PROCESSING_QUEUE,
                timeout=_BLPOP_TIMEOUT,
                src='LEFT',
                dest='RIGHT',
            )
            if job_data:
                job = json.loads(job_data)
                # Stash the exact raw string so ack/nack can remove it reliably
                job['_raw'] = job_data
                return job
            return None
        except redis.RedisError as e:
            logger.error(f"Redis error during dequeue: {e}")
            raise

    @staticmethod
    def ack_job(job: dict):
        """Remove job from processing queue after successful completion."""
        try:
            raw = job.get('_raw') or json.dumps({k: v for k, v in job.items() if k != '_raw'})
            DeploymentQueue.get_redis().lrem(DeploymentQueue.PROCESSING_QUEUE, 1, raw)
        except Exception as e:
            logger.warning(f"Failed to ack job {job.get('app_id')}: {e}")

    @staticmethod
    def nack_job(job: dict):
        """On failure: retry up to MAX_RETRIES, then move to DLQ.
        Re-enqueue FIRST before removing from processing queue to prevent job loss
        if the second Redis operation fails.
        """
        r = DeploymentQueue.get_redis()
        raw = job.pop('_raw', None) or json.dumps(job)

        retry_count = job.get("retry_count", 0) + 1
        job["retry_count"] = retry_count
        dest_queue = DeploymentQueue.QUEUE_NAME if retry_count <= DeploymentQueue.MAX_RETRIES else DeploymentQueue.DLQ_NAME

        try:
            # Push to destination FIRST — if this fails, job stays in processing queue (safe)
            r.rpush(dest_queue, json.dumps(job))
            # Only remove from processing after successful re-enqueue
            r.lrem(DeploymentQueue.PROCESSING_QUEUE, 1, raw)
            if dest_queue == DeploymentQueue.QUEUE_NAME:
                logger.warning(f"Re-enqueued job {job.get('app_id')} (retry {retry_count}/{DeploymentQueue.MAX_RETRIES})")
            else:
                logger.error(f"Job {job.get('app_id')} moved to DLQ after {DeploymentQueue.MAX_RETRIES} retries")
        except redis.RedisError as e:
            logger.critical(f"CRITICAL: nack_job Redis failure for {job.get('app_id')} — job state uncertain: {e}")
            raise
