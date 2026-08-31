import os
import time
import uuid
import signal
import logging
import threading
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, Future
from django.core.management.base import BaseCommand
from django.db import connections, transaction
from api.services.infra_queue import DB_LOCK_STALENESS_SECONDS

os.environ['DB_CONN_MAX_AGE'] = '0'

logger = logging.getLogger(__name__)


def _publish_infra_deleted(user_id, infra_id):
    """Propagate a destroy-driven row deletion to read-models (application-service)
    so they drop the infra; without this a reused (user, name) later collides."""
    try:
        from api.messaging.producer.producer import infra_producer
        infra_producer.publish_infrastructure_deleted(user_id=user_id, infra_id=infra_id)
    except Exception:
        logger.error(f"Failed to publish infrastructure.deleted for {infra_id}", exc_info=True)

MAX_PROVISION_WORKERS = int(os.environ.get('INFRA_MAX_PROVISION_WORKERS', '5'))
MAX_DESTROY_WORKERS = int(os.environ.get('INFRA_MAX_DESTROY_WORKERS', '3'))
SHUTDOWN_TIMEOUT = int(os.environ.get('INFRA_SHUTDOWN_TIMEOUT', '300'))
PROVISION_PER_DESTROY = int(os.environ.get('INFRA_PROVISION_PER_DESTROY', '1'))
# The reaper re-enqueues a job whose lock hasn't been heartbeated for this long. Clamped to the DB
# lock staleness: re-enqueuing sooner than acquire_db_lock will grant the lock just churns the
# queue, and both windows must agree on when a job counts as crashed.
STUCK_THRESHOLD = max(int(os.environ.get('INFRA_STUCK_THRESHOLD_SECONDS', str(DB_LOCK_STALENESS_SECONDS))),
                      DB_LOCK_STALENESS_SECONDS)
REAP_INTERVAL = int(os.environ.get('INFRA_REAP_INTERVAL_SECONDS', '120'))
# The running/queued job refreshes its lock this often; must be well under DB_LOCK_STALENESS_SECONDS
# so a live job never looks crashed to the reaper or acquire_db_lock.
LOCK_HEARTBEAT_SECONDS = int(os.environ.get('INFRA_LOCK_HEARTBEAT_SECONDS', '60'))
# A job the reaper has re-driven this many times is treated as poison and parked in ERROR rather
# than re-run against the customer account indefinitely.
MAX_REAP_ATTEMPTS = int(os.environ.get('INFRA_MAX_REAP_ATTEMPTS', '5'))


class LockHeartbeat:
    """Keeps a dispatched job's DB lock fresh from dispatch until the job finishes, so a job that
    is running (or waiting in the executor) is never mistaken for a crashed one and stolen by
    another worker — which would run terraform twice against a single customer account.

    The whole crash-safety design leans on this thread staying alive, so a transient DB error
    (connection drop, failover — plausible across an hours-long apply with per-thread connections)
    must NOT kill it: it recycles the connection and keeps beating."""

    def __init__(self, infra_id, lock_token, interval=LOCK_HEARTBEAT_SECONDS):
        self._infra_id = infra_id
        self._lock_token = lock_token
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        from api.services.infra_queue import InfraQueue
        try:
            while not self._stop.wait(self._interval):
                try:
                    if not InfraQueue.refresh_db_lock(self._infra_id, self._lock_token):
                        logger.warning(f"Heartbeat: lock for {self._infra_id} no longer owned")
                except Exception:
                    logger.warning(f"Heartbeat refresh failed for {self._infra_id}; retrying", exc_info=True)
                    _close_db()
        finally:
            _close_db()

    def stop(self):
        self._stop.set()


def reap_stuck_environments(stale_threshold_seconds):
    """Re-enqueue environments stuck mid-flight past the staleness window.

    A hard worker crash loses the in-memory job while the Environment stays PROVISIONING/
    DESTROYING, and startup recovery only fires on the next restart. Running this periodically
    re-drives a stuck row without waiting for a bounce. Re-execution is safe: a live job heartbeats
    its lock (LockHeartbeat), so only a genuinely crashed one crosses the window here, and
    acquire_db_lock's matching staleness gate is the single arbiter of who actually runs.
    """
    from datetime import timedelta
    from django.db.models import Q
    from django.utils import timezone
    from api.models.environment import Environment
    from api.services.infra_queue import InfraQueue
    from api.services.notification import NotificationService

    cutoff = timezone.now() - timedelta(seconds=stale_threshold_seconds)
    # A stale DB lock means a crashed run. A null DB lock is ambiguous: it's either a job still
    # waiting in the queue (its Redis dedup key is set) or one whose queue entry was lost (key
    # gone). The dedup key — not a timestamp — is the authoritative signal, so the null case is
    # filtered per-row below (updated_at is unreliable: save(update_fields=['status']) never bumps
    # it, so an ACTIVE->DESTROYING row would look aged the instant it's enqueued).
    stuck = Environment.objects.filter(
        status__in=['PROVISIONING', 'DESTROYING'],
    ).filter(
        Q(locked_at__lt=cutoff) | Q(locked_at__isnull=True)
    ).select_related('infrastructure')

    reaped = 0
    for env in stuck:
        infra_id = str(env.infrastructure_id)
        if env.locked_at is None and InfraQueue.has_lock(infra_id):
            # No DB lock but the dedup key is still set: the job is queued and waiting its turn,
            # not lost. Reaping now would just duplicate it.
            continue
        if InfraQueue.bump_reap_count(infra_id) > MAX_REAP_ATTEMPTS:
            logger.error(f"Reaper giving up on {infra_id} after {MAX_REAP_ATTEMPTS} attempts; parking in ERROR")
            Environment.objects.filter(infrastructure_id=infra_id).update(
                status='ERROR', locked_at=None, locked_by=None,
                error_message=f"{env.status} abandoned after {MAX_REAP_ATTEMPTS} recovery attempts",
            )
            InfraQueue.release_lock(infra_id)
            InfraQueue.clear_reap_count(infra_id)
            try:
                infra = env.infrastructure
                NotificationService.send_provision_failure(
                    str(infra.user_id), infra_id, infra.name,
                    f"{env.status.capitalize()} could not be recovered")
            except Exception:
                logger.error(f"Failed to notify abandonment for {infra_id}", exc_info=True)
            continue
        # Release only the Redis dedup lock so the job can be re-queued. The DB lock is left intact:
        # acquire_db_lock's staleness window is the single execution gate, so a genuinely-live job
        # keeps a heartbeated lock the re-dispatch can't steal.
        InfraQueue.release_lock(infra_id)
        if env.status == 'DESTROYING':
            InfraQueue.enqueue_destroy(infra_id)
        else:
            InfraQueue.enqueue_provision(infra_id)
        logger.warning(f"Reaper re-enqueued stuck {env.status} environment {infra_id}")
        reaped += 1
    return reaped


def ensure_infra_created_published(infra):
    """Self-heal a swallowed onboarding-callback publish. The callback publishes infra.created
    best-effort; on a broker hiccup it logs and proceeds, leaving read-models (application-service)
    without the infra so later app deploys fail. Re-publish when provisioning runs — the consumer
    is idempotent on infra_id, so a redundant event is harmless."""
    if not infra.is_cloud_authenticated:
        return
    try:
        from api.messaging.producer.producer import infra_producer
        infra_producer.publish_infra_created(
            user_id=infra.user_id, infra_id=infra.id, name=infra.name,
            cloud_provider=infra.cloud_provider, compute_type=infra.compute_type,
            max_cpu=infra.max_cpu, max_memory=infra.max_memory,
            code=infra.code, is_cloud_authenticated=infra.is_cloud_authenticated, is_mock=infra.is_mock,
            metadata=infra.metadata or {},
        )
    except Exception:
        logger.error(f"Failed to (re)publish infra.created for {infra.id}", exc_info=True)


def _close_db():
    """Close only unusable/obsolete connections rather than all connections."""
    for conn in connections.all():
        conn.close_if_unusable_or_obsolete()


class Command(BaseCommand):
    help = 'Run the infrastructure provisioning worker'

    def handle(self, *args, **options):
        from django.conf import settings
        from api.services.infra_queue import InfraQueue
        from api.services.terraform_worker import TerraformWorker
        from api.services.notification import NotificationService
        from api.models.infrastructure import Infrastructure
        from api.models.environment import Environment
        from shared.enums.orchestrator import ComputeType

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
        pending_futures: list[Future] = []

        logger.info(f"Infrastructure worker {worker_id} started "
                    f"(provision={MAX_PROVISION_WORKERS}, destroy={MAX_DESTROY_WORKERS})")

        # Re-enqueue stuck jobs — only one worker should do this at startup
        recovery_lock_key = "infra:worker:recovery_lock"
        from api.services.infra_queue import _redis as _get_redis, PROVISION_QUEUE
        r = _get_redis()
        if r.set(recovery_lock_key, worker_id, nx=True, ex=60):
            try:
                from api.services.infra_queue import DESTROY_QUEUE
                for env in Environment.objects.filter(status__in=['PENDING', 'PROVISIONING']).select_related('infrastructure'):
                    infra_id_str = str(env.infrastructure_id)
                    InfraQueue.release_lock(infra_id_str)
                    already_queued = any(infra_id_str in item for item in r.lrange(PROVISION_QUEUE, 0, -1))
                    if not already_queued:
                        InfraQueue.enqueue_provision(infra_id_str)
                        logger.info(f"Re-enqueued provision for {infra_id_str}")

                for env in Environment.objects.filter(status='DESTROYING').select_related('infrastructure'):
                    infra_id_str = str(env.infrastructure_id)
                    InfraQueue.release_lock(infra_id_str)
                    InfraQueue.release_db_lock(infra_id_str)  # clear any stale DB lock from crashed worker
                    already_queued = any(infra_id_str in item for item in r.lrange(DESTROY_QUEUE, 0, -1))
                    if not already_queued:
                        InfraQueue.enqueue_destroy(infra_id_str)
                        logger.info(f"Re-enqueued destroy for {infra_id_str}")
            finally:
                if r.get(recovery_lock_key) == worker_id:
                    r.delete(recovery_lock_key)
        else:
            logger.info(f"Worker {worker_id} skipping recovery — another worker is handling it")

        def run_provision(infra_id, lock_token):
            infra = None
            try:
                infra = Infrastructure.objects.get(id=infra_id)
                # Dispatch-time kill switch: create-time gating alone isn't enough — the reaper
                # and startup recovery re-enqueue independently, so an EKS infra created while
                # the flag was on would keep re-provisioning after it's turned off. Parking in
                # ERROR terminates both re-enqueue loops instead of silently spinning.
                if infra.compute_type == ComputeType.EKS and not settings.EKS_ENABLED:
                    error_message = "EKS provisioning is disabled (EKS_ENABLED=false)"
                    logger.error(f"{error_message}; parking infra {infra_id} in ERROR")
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status='ERROR', error_message=error_message,
                    )
                    InfraQueue.clear_reap_count(infra_id)
                    NotificationService.send_provision_failure(
                        str(infra.user_id), infra_id, infra.name, error_message)
                    return
                ensure_infra_created_published(infra)
                TerraformWorker.provision(infra_id)
                env = Environment.objects.get(infrastructure_id=infra_id)
                if env.status == 'ACTIVE':
                    InfraQueue.clear_reap_count(infra_id)
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
                InfraQueue.release_db_lock(infra_id, lock_token)
                InfraQueue.release_lock(infra_id)
                _close_db()

        def run_destroy(infra_id, lock_token):
            infra = None
            try:
                # Call destroy FIRST so it can handle missing Infrastructure rows
                TerraformWorker.destroy(infra_id)

                # Only fetch Infrastructure afterward for post-destroy work
                try:
                    infra = Infrastructure.objects.get(id=infra_id)
                except Infrastructure.DoesNotExist:
                    logger.warning(f"Infrastructure {infra_id} already deleted during destroy")
                    try:
                        deleted, _ = Environment.objects.filter(infrastructure_id=infra_id).delete()
                        if deleted:
                            logger.info(f"Cleaned up {deleted} orphaned Environment record(s) for {infra_id}")
                    except Exception as env_exc:
                        logger.error(f"Failed to clean up Environment for {infra_id}: {env_exc}", exc_info=True)
                    return

                try:
                    env = Environment.objects.get(infrastructure_id=infra_id)
                    if env.status == 'DESTROYED':
                        InfraQueue.clear_reap_count(infra_id)
                        NotificationService.send_destroy_success(str(infra.user_id), infra_id, infra.name)
                        user_id = infra.user_id
                        with transaction.atomic():
                            env.delete()
                            infra.delete()
                        logger.info(f"Deleted DB records for {infra_id}")
                        _publish_infra_deleted(user_id, infra_id)
                    else:
                        NotificationService.send_destroy_failure(str(infra.user_id), infra_id, infra.name, env.error_message or 'Unknown error')
                except Environment.DoesNotExist:
                    NotificationService.send_destroy_success(str(infra.user_id), infra_id, infra.name)
                    user_id = infra.user_id
                    with transaction.atomic():
                        infra.delete()
                    logger.info(f"Deleted DB records for {infra_id}")
                    _publish_infra_deleted(user_id, infra_id)
            except Exception as e:
                logger.error(f"Destroy failed for {infra_id}: {e}", exc_info=True)
                if infra:
                    try:
                        NotificationService.send_destroy_failure(str(infra.user_id), infra_id, infra.name, str(e))
                    except Exception:
                        pass
            finally:
                InfraQueue.release_db_lock(infra_id, lock_token)
                InfraQueue.release_lock(infra_id)
                _close_db()

        # A lock is owned by a per-dispatch token, not the process id, so if this same worker
        # re-acquires an infra it previously ran, the earlier job's release can't wipe the new
        # job's lock (its token differs). worker_id stays as the token prefix for traceability.
        def _new_lock_token():
            return f"{worker_id}:{uuid.uuid4().hex[:8]}"

        def dispatch_provision():
            job = InfraQueue.dequeue_provision(timeout=1)
            if not job:
                return False
            infra_id = job['infra_id']
            lock_token = _new_lock_token()
            if not InfraQueue.acquire_db_lock(infra_id, lock_token):
                logger.warning(f"Could not acquire lock for {infra_id}, re-enqueueing")
                InfraQueue.enqueue_provision(infra_id)
                return False
            heartbeat = LockHeartbeat(infra_id, lock_token)
            heartbeat.start()
            try:
                future: Future = provision_pool.submit(run_provision, infra_id, lock_token)
                future.add_done_callback(lambda f: (heartbeat.stop(), _log_future_exception(f, infra_id, 'provision')))
                pending_futures.append(future)
                return True
            except Exception:
                heartbeat.stop()
                InfraQueue.release_db_lock(infra_id, lock_token)
                InfraQueue.release_lock(infra_id)
                logger.error(f"Failed to submit provision job for {infra_id}", exc_info=True)
                raise

        def dispatch_destroy():
            job = InfraQueue.dequeue_destroy(timeout=0)
            if not job:
                return False
            infra_id = job['infra_id']
            lock_token = _new_lock_token()
            if not InfraQueue.acquire_db_lock(infra_id, lock_token):
                logger.warning(f"Could not acquire destroy lock for {infra_id}, re-enqueueing")
                InfraQueue.enqueue_destroy(infra_id)
                return False
            heartbeat = LockHeartbeat(infra_id, lock_token)
            heartbeat.start()
            try:
                future: Future = destroy_pool.submit(run_destroy, infra_id, lock_token)
                future.add_done_callback(lambda f: (heartbeat.stop(), _log_future_exception(f, infra_id, 'destroy')))
                pending_futures.append(future)
                return True
            except Exception:
                heartbeat.stop()
                InfraQueue.release_db_lock(infra_id, lock_token)
                InfraQueue.release_lock(infra_id)
                logger.error(f"Failed to submit destroy job for {infra_id}", exc_info=True)
                raise

        reap_lock_key = "infra:worker:reap_lock"
        provision_counter = 0
        last_reap = time.monotonic()
        while running:
            try:
                # Periodically re-drive stuck jobs. A short-lived Redis lock rate-limits it to
                # once per interval across all workers, so a fleet doesn't reap in lockstep.
                if time.monotonic() - last_reap >= REAP_INTERVAL:
                    last_reap = time.monotonic()
                    if r.set(reap_lock_key, worker_id, nx=True, ex=max(REAP_INTERVAL - 5, 10)):
                        try:
                            reap_stuck_environments(STUCK_THRESHOLD)
                        except Exception:
                            logger.error("Reaper sweep failed", exc_info=True)

                # Always drain destroy queue first (non-blocking), then provision
                had_destroy = dispatch_destroy()
                if had_destroy:
                    provision_counter = 0
                    continue

                if dispatch_provision():
                    provision_counter += 1
                else:
                    # Nothing in either queue — short sleep to avoid busy-loop
                    time.sleep(1)
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

        _, not_done = concurrent.futures.wait(pending_futures, timeout=SHUTDOWN_TIMEOUT)
        if not_done:
            logger.warning(f"Shutdown timeout reached, {len(not_done)} job(s) may be interrupted")
        logger.info(f"Worker {worker_id} stopped")


def _log_future_exception(future: Future, infra_id: str, op: str):
    exc = future.exception()
    if exc:
        logger.error(f"Unhandled exception in {op} task for {infra_id}: {exc}", exc_info=exc)
