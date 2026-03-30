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

    @staticmethod
    def get_redis() -> redis.Redis:
        return redis.Redis(connection_pool=_pool)

    @staticmethod
    def enqueue_deployment(app_id: str, infrastructure_id: str = None):
        try:
            job = {"app_id": str(app_id), "action": "deploy"}
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
            }
            DeploymentQueue.get_redis().rpush(DeploymentQueue.QUEUE_NAME, json.dumps(job))
            logger.info(f"Enqueued cleanup for application {app_id}")
        except Exception as e:
            logger.error(f"Failed to enqueue cleanup: {e}")
            raise

    @staticmethod
    def dequeue_deployment():
        try:
            result = redis.Redis(connection_pool=_blocking_pool).blpop(DeploymentQueue.QUEUE_NAME, timeout=_BLPOP_TIMEOUT)
            if result:
                _, job_data = result
                return json.loads(job_data)
            return None
        except redis.RedisError as e:
            logger.error(f"Redis error during dequeue: {e}")
            raise  # let the worker's except handler back off and retry
