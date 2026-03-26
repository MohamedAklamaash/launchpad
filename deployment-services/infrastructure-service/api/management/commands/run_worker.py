import os
import time
import uuid
import signal
import logging
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, Future
from django.core.management.base import BaseCommand
from django.db import connections, transaction

os.environ['DB_CONN_MAX_AGE'] = '0'

logger = logging.getLogger(__name__)

MAX_PROVISION_WORKERS = int(os.environ.get('INFRA_MAX_PROVISION_WORKERS', '5'))
MAX_DESTROY_WORKERS = int(os.environ.get('INFRA_MAX_DESTROY_WORKERS', '3'))
SHUTDOWN_TIMEOUT = int(os.environ.get('INFRA_SHUTDOWN_TIMEOUT', '60'))
PROVISION_PER_DESTROY = int(os.environ.get('INFRA_PROVISION_PER_DESTROY', '3'))


def _close_db():
    """Close only unusable/obsolete connections rather than all connections."""
    for conn in connections.all():
        conn.close_if_unusable_or_obsolete()


@contextmanager
def _infra_lock(infra_queue, infra_id, worker_id):
    """Context manager that acquires and guarantees release of both locks."""
    acquired = infra_queue.acquire_db_lock(infra_id, worker_id)
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        infra_queue.release_db_lock(infra_id)
        infra_queue.release_lock(infra_id)


class Command(BaseCommand):
    help = 'Run the infrastructure provisioning worker'

    def handle(self, *args, **options):
        from api.services.infra_queue import InfraQueue
        from api.services.terraform_worker import TerraformWorker
        from api.services.notification import NotificationService
        from api.models.infrastructure import Infrastructure
        from api.models.environment import Environment

        worker_id = str(uuid.uuid4())[:8]
        running = True

        def _stop(sig, frame):
            nonlocal running
            logger.info(f"Worker {worker_id} shutting down...")
            running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        provision_pool = ThreadPoolExecutor(max_workers=MAX_PROVISION_WORKERS, thread_name_prefix='provision')
        destroy_pool = ThreadPoolExecutor(max_workers=MAX_DESTROY_WORKERS, thread_name_prefix='destroy')

        logger.info(f"Infrastructure worker {worker_id} started "
                    f"(provision={MAX_PROVISION_WORKERS}, destroy={MAX_DESTROY_WORKERS})")

        # Re-enqueue stuck jobs — only one worker should do this at startup
        recovery_lock_key = "infra:worker:recovery_lock"
        from api.services.infra_queue import _redis as _get_redis
        r = _get_redis()
        if r.set(recovery_lock_key, worker_id, nx=True, ex=60):
            try:
                for env in Environment.objects.filter(status__in=['PENDING', 'PROVISIONING']).select_related('infrastructure'):
                    InfraQueue.release_lock(str(env.infrastructure_id))
                    InfraQueue.enqueue_provision(str(env.infrastructure_id))
                    logger.info(f"Re-enqueued {env.infrastructure_id}")
            finally:
                if r.get(recovery_lock_key) == worker_id.encode():
                    r.delete(recovery_lock_key)
        else:
            logger.info(f"Worker {worker_id} skipping recovery — another worker is handling it")

        def run_provision(infra_id):
            infra = None
            try:
                infra = Infrastructure.objects.get(id=infra_id)
                TerraformWorker.provision(infra_id)
                env = Environment.objects.get(infrastructure_id=infra_id)
                if env.status == 'ACTIVE':
                    NotificationService.send_provision_success(str(infra.user_id), infra_id, infra.name)
                elif env.status == 'ERROR':
                    NotificationService.send_provision_failure(str(infra.user_id), infra_id, infra.name, env.error_message or 'Unknown error')
            except Exception as e:
                logger.error(f"Provision failed for {infra_id}: {e}", exc_info=True)
                if infra:
                    try:
                        NotificationService.send_provision_failure(str(infra.user_id), infra_id, infra.name, str(e))
                    except Exception:
                        pass
            finally:
                _close_db()

        def run_destroy(infra_id):
            infra = None
            try:
                # Call destroy FIRST so it can handle missing Infrastructure rows
                TerraformWorker.destroy(infra_id)

                # Only fetch Infrastructure afterward for post-destroy work
                try:
                    infra = Infrastructure.objects.get(id=infra_id)
                except Infrastructure.DoesNotExist:
                    logger.warning(f"Infrastructure {infra_id} already deleted during destroy")
                    return

                try:
                    env = Environment.objects.get(infrastructure_id=infra_id)
                    if env.status == 'DESTROYED':
                        NotificationService.send_destroy_success(str(infra.user_id), infra_id, infra.name)
                        with transaction.atomic():
                            env.delete()
                            infra.delete()
                        logger.info(f"Deleted DB records for {infra_id}")
                    else:
                        NotificationService.send_destroy_failure(str(infra.user_id), infra_id, infra.name, env.error_message or 'Unknown error')
                except Environment.DoesNotExist:
                    NotificationService.send_destroy_success(str(infra.user_id), infra_id, infra.name)
                    infra.delete()
            except Exception as e:
                logger.error(f"Destroy failed for {infra_id}: {e}", exc_info=True)
                if infra:
                    try:
                        NotificationService.send_destroy_failure(str(infra.user_id), infra_id, infra.name, str(e))
                    except Exception:
                        pass
            finally:
                _close_db()

        def dispatch_provision():
            job = InfraQueue.dequeue_provision(timeout=1)
            if not job:
                return False
            infra_id = job['infra_id']
            with _infra_lock(InfraQueue, infra_id, worker_id) as acquired:
                if not acquired:
                    logger.warning(f"Could not acquire lock for {infra_id}, skipping")
                    return False
                try:
                    future: Future = provision_pool.submit(run_provision, infra_id)
                    future.add_done_callback(lambda f: _log_future_exception(f, infra_id, 'provision'))
                    return True
                except Exception:
                    # Lock released by context manager on exit
                    logger.error(f"Failed to submit provision job for {infra_id}", exc_info=True)
                    raise

        def dispatch_destroy():
            job = InfraQueue.dequeue_destroy(timeout=0)
            if not job:
                return False
            infra_id = job['infra_id']
            with _infra_lock(InfraQueue, infra_id, worker_id) as acquired:
                if not acquired:
                    logger.warning(f"Could not acquire destroy lock for {infra_id}, skipping")
                    return False
                try:
                    future: Future = destroy_pool.submit(run_destroy, infra_id)
                    future.add_done_callback(lambda f: _log_future_exception(f, infra_id, 'destroy'))
                except Exception:
                    logger.error(f"Failed to submit destroy job for {infra_id}", exc_info=True)
                    raise
            return True

        provision_counter = 0
        while running:
            try:
                if provision_counter < PROVISION_PER_DESTROY:
                    if dispatch_provision():
                        provision_counter += 1
                else:
                    had_destroy = dispatch_destroy()
                    if had_destroy:
                        provision_counter = 0
                    else:
                        dispatch_provision()
            except Exception as e:
                from redis.exceptions import TimeoutError as RedisTimeout
                if isinstance(e, (TimeoutError, RedisTimeout)):
                    logger.warning(f"Redis timeout in worker loop, continuing: {e}")
                else:
                    logger.error(f"Worker loop error: {e}", exc_info=True)
                _close_db()

        logger.info("Waiting for in-flight jobs to complete...")
        provision_pool.shutdown(wait=False)
        destroy_pool.shutdown(wait=False)

        start_wait = time.time()
        while time.time() - start_wait < SHUTDOWN_TIMEOUT:
            if provision_pool._work_queue.empty() and destroy_pool._work_queue.empty():
                break
            time.sleep(1)
        else:
            logger.warning("Shutdown timeout reached, some provision and destroy jobs may be interrupted")
        logger.info(f"Worker {worker_id} stopped")


def _log_future_exception(future: Future, infra_id: str, op: str):
    exc = future.exception()
    if exc:
        logger.error(f"Unhandled exception in {op} task for {infra_id}: {exc}", exc_info=exc)