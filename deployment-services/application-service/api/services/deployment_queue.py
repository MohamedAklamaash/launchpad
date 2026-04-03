import socket
import redis
import json
import logging
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
    def enqueue_deployment(app_id: str, infrastructure_id: str = None):
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
    def enqueue_cleanup(app_id: str, infrastructure_id: str, service_arn: str = None,
                        listener_rule_arn: str = None, target_group_arn: str = None,
                        task_definition_arn: str = None):
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
    def recover_processing_jobs():
        """On worker startup, move any jobs stuck in the processing queue back to main queue."""
        r = DeploymentQueue.get_redis()
        recovered = 0
        while True:
            job_data = r.rpoplpush(DeploymentQueue.PROCESSING_QUEUE, DeploymentQueue.QUEUE_NAME)
            if not job_data:
                break
            recovered += 1
        if recovered:
            logger.warning(f"Recovered {recovered} in-flight job(s) from processing queue")

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
                return json.loads(job_data)
            return None
        except redis.RedisError as e:
            logger.error(f"Redis error during dequeue: {e}")
            raise

    @staticmethod
    def ack_job(job: dict):
        """Remove job from processing queue after successful completion."""
        try:
            DeploymentQueue.get_redis().lrem(
                DeploymentQueue.PROCESSING_QUEUE, 1, json.dumps(job, sort_keys=True)
            )
        except Exception as e:
            logger.warning(f"Failed to ack job {job.get('app_id')}: {e}")

    @staticmethod
    def nack_job(job: dict):
        """On failure: retry up to MAX_RETRIES, then move to DLQ."""
        r = DeploymentQueue.get_redis()
        job_str = json.dumps(job, sort_keys=True)
        r.lrem(DeploymentQueue.PROCESSING_QUEUE, 1, job_str)

        retry_count = job.get("retry_count", 0) + 1
        if retry_count <= DeploymentQueue.MAX_RETRIES:
            job["retry_count"] = retry_count
            r.rpush(DeploymentQueue.QUEUE_NAME, json.dumps(job))
            logger.warning(f"Re-enqueued job {job.get('app_id')} (retry {retry_count}/{DeploymentQueue.MAX_RETRIES})")
        else:
            r.rpush(DeploymentQueue.DLQ_NAME, json.dumps(job))
            logger.error(f"Job {job.get('app_id')} moved to DLQ after {DeploymentQueue.MAX_RETRIES} retries")
