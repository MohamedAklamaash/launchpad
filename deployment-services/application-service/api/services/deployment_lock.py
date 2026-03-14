import redis
import logging
import os

logger = logging.getLogger(__name__)

_pool = redis.ConnectionPool.from_url(
    os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
    decode_responses=True,
    max_connections=10,
)


class DeploymentLock:
    """Distributed lock for application deployments"""

    def __init__(self):
        self.redis_client = redis.Redis(connection_pool=_pool)
        self.lock_timeout = int(os.environ.get('DEPLOYMENT_LOCK_TIMEOUT', '300'))

    def acquire(self, app_id, worker_id):
        lock_key = f"deployment:lock:{app_id}"
        acquired = self.redis_client.set(lock_key, worker_id, nx=True, ex=self.lock_timeout)
        if acquired:
            logger.info(f"Worker {worker_id} acquired lock for app {app_id}")
            return True
        current_owner = self.redis_client.get(lock_key)
        logger.warning(f"App {app_id} is locked by {current_owner}")
        return False

    def release(self, app_id, worker_id):
        lock_key = f"deployment:lock:{app_id}"
        current_owner = self.redis_client.get(lock_key)
        if current_owner == worker_id:
            self.redis_client.delete(lock_key)
            logger.info(f"Worker {worker_id} released lock for app {app_id}")
            return True
        logger.warning(f"Worker {worker_id} cannot release lock for app {app_id} (owned by {current_owner})")
        return False

    def is_locked(self, app_id):
        return self.redis_client.exists(f"deployment:lock:{app_id}") > 0
