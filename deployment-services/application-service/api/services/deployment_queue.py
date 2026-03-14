import socket
import redis
import json
import logging
from api.common.envs.application import app_config

logger = logging.getLogger(__name__)

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


class DeploymentQueue:
    QUEUE_NAME = "deployment_queue"

    @staticmethod
    def get_redis() -> redis.Redis:
        return redis.Redis(connection_pool=_pool)

    @staticmethod
    def enqueue_deployment(app_id: str):
        try:
            r = DeploymentQueue.get_redis()
            job = {"app_id": str(app_id), "action": "deploy"}
            r.rpush(DeploymentQueue.QUEUE_NAME, json.dumps(job))
            logger.info(f"Enqueued deployment for application {app_id}")
        except Exception as e:
            logger.error(f"Failed to enqueue deployment: {e}")
            raise

    @staticmethod
    def dequeue_deployment():
        try:
            r = DeploymentQueue.get_redis()
            result = r.blpop(DeploymentQueue.QUEUE_NAME, timeout=5)
            if result:
                _, job_data = result
                return json.loads(job_data)
            return None
        except Exception as e:
            logger.error(f"Failed to dequeue deployment: {e}")
            return None
