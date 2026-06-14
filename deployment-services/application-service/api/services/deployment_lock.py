import redis
import logging
import os
from api.services.deployment_queue import _pool

logger = logging.getLogger(__name__)

# Compare-and-act in a single round trip so we only ever delete/extend a lock we
# still own. A plain get()-then-delete()/expire() is a TOCTOU: if our TTL lapsed
# and another worker grabbed the lock between the two calls, we'd clobber theirs
# and two workers would deploy the same app.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""
_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
else
    return 0
end
"""


class DeploymentLock:
    """Distributed lock for application deployments"""

    def __init__(self):
        self.redis_client = redis.Redis(connection_pool=_pool)
        # Initial TTL is 300s. The heartbeat thread renews it every 60s for the
        # duration of the deploy, so long-running builds never expire the lock.
        self.lock_timeout = int(os.environ.get('DEPLOYMENT_LOCK_TIMEOUT', '300'))
        self._heartbeat_threads: dict = {}
        self._release_script = self.redis_client.register_script(_RELEASE_LUA)
        self._renew_script = self.redis_client.register_script(_RENEW_LUA)

    def acquire(self, app_id, worker_id):
        lock_key = f"deployment:lock:{app_id}"
        acquired = self.redis_client.set(lock_key, worker_id, nx=True, ex=self.lock_timeout)
        if acquired:
            logger.info(f"Worker {worker_id} acquired lock for app {app_id}")
            self._start_heartbeat(app_id, worker_id)
            return True
        current_owner = self.redis_client.get(lock_key)
        logger.warning(f"App {app_id} is locked by {current_owner}")
        return False

    def release(self, app_id, worker_id):
        self._stop_heartbeat(app_id)
        lock_key = f"deployment:lock:{app_id}"
        released = self._release_script(keys=[lock_key], args=[worker_id])
        if released:
            logger.info(f"Worker {worker_id} released lock for app {app_id}")
            return True
        logger.warning(f"Worker {worker_id} cannot release lock for app {app_id} (not owner / already expired)")
        return False

    def is_locked(self, app_id):
        return self.redis_client.exists(f"deployment:lock:{app_id}") > 0

    def _start_heartbeat(self, app_id, worker_id):
        """Renew lock TTL every 60s so long-running deploys don't expire."""
        import threading
        stop_event = threading.Event()
        self._heartbeat_threads[app_id] = stop_event

        def _renew():
            lock_key = f"deployment:lock:{app_id}"
            while not stop_event.wait(60):
                # Atomically extend only if we still own it; stop the heartbeat if not.
                if not self._renew_script(keys=[lock_key], args=[worker_id, self.lock_timeout]):
                    break

        t = threading.Thread(target=_renew, daemon=True, name=f"heartbeat-{app_id}")
        t.start()

    def _stop_heartbeat(self, app_id):
        event = self._heartbeat_threads.pop(app_id, None)
        if event:
            event.set()
