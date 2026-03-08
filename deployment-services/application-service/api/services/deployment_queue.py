import redis
import json
import logging
from api.common.envs.application import app_config

logger = logging.getLogger(__name__)

class DeploymentQueue:
    QUEUE_NAME = "deployment_queue"
    
    @staticmethod
    def get_redis():
        return redis.Redis(
            host=app_config.redis_host,
            port=app_config.redis_port,
            password=app_config.redis_password,
            db=app_config.redis_db,
            decode_responses=True
        )
    
    @staticmethod
    def enqueue_deployment(app_id: str):
        """Add deployment job to queue"""
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
        """Get next deployment job from queue (blocking)"""
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
