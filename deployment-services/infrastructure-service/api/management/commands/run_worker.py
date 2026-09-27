import concurrent.futures
import logging
import os
import signal
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta

from api.services.infra_queue import DB_LOCK_STALENESS_SECONDS
from django.core.management.base import BaseCommand
from django.db import connections, transaction
from django.utils import timezone

os.environ['DB_CONN_MAX_AGE'] = '0'

logger = logging.getLogger(__name__)


def _has_live_dns_state(infra_id) -> bool:
    """Defense in depth: TerraformWorker.destroy() already waits for the platform DNS
    writer to confirm teardown before Environment.status can reach DESTROYED (see
    request_and_await_dns_teardown), so this should normally find nothing. Checked again
    here because this is the point of no return for the Infrastructure row itself — the
    same ledger this service can independently confirm, never Environment.status."""
    from api.services.platform_dns.teardown import has_live_dns_state
    return has_live_dns_state(infra_id)


def _refuse_hard_delete_for_live_dns(infra_id) -> bool:
    """True means "refuse the hard delete". When refusing, also republishes a reconcile
    request (coalesce=False) — without this, a blocked delete would just sit until some
    unrelated trigger happens to re-poke the writer, rather than actually retrying."""
    if not _has_live_dns_state(infra_id):
        return False
    from api.services.platform_dns.producer import request_dns_reconcile
    try:
        request_dns_reconcile(infra_id, coalesce=False)
    except Exception:
        logger.warning(f"platform DNS reconcile request failed for {infra_id} (non-fatal)", exc_info=True)
    return True


def _publish_infra_deleted(user_id, infra_id):
    """Propagate a destroy-driven row deletion to read-models (application-service)
    so they drop the infra; without this a reused (user, name) later collides."""
    try:
        from api.messaging.producer.producer import infra_producer
        infra_producer.publish_infrastructure_deleted(user_id=user_id, infra_id=infra_id)
    except Exception:
        logger.exception(f"Failed to publish infrastructure.deleted for {infra_id}")

CERT_RECHECK_TIME_BUDGET_SECONDS = int(os.environ.get('INFRA_CERT_RECHECK_TIME_BUDGET_SECONDS', '20'))


def _cert_recheck_eligible(infra, env) -> bool:
    """Shared eligibility gate for every action a cert-recheck tick can take against an
    infra. `dns_teardown_requested_at` is monotonic and one-way (see
    Infrastructure.mark_dns_teardown_requested's docstring): once set it never clears, so
    an infra past it — or an environment not currently ACTIVE (DESTROYING/DESTROYED after
    a completed or timed-out teardown, ERROR, PROVISIONING/UPDATING already mid-flight) —
    must never have this background loop call enqueue_provision on it. Without this gate,
    a certificate that happens to flip to ISSUED after teardown was requested (teardown
    timed out, or the destroy itself failed and parked the environment in ERROR) would
    resurrect the infrastructure via a fresh terraform apply.

    `exited_at` (F6) is checked too, belt-and-suspenders: complete_exit always calls
    request_and_await_dns_teardown (which sets dns_teardown_requested_at) before ever
    setting exited_at, so this should already be implied by the first check — but an
    exited infra is exactly the kind of "must never resurrect" case this gate exists for,
    so it's asserted directly rather than relying on that ordering staying true forever.
    """
    return (
        infra.dns_teardown_requested_at is None
        and infra.exited_at is None
        and env is not None and env.status == 'ACTIVE'
    )


def check_pending_certificates():
    """Advance PENDING InfrastructureCertificate rows toward ISSUED/FAILED, and recover a
    lost enqueue_provision for a row that is already ISSUED but never got its 443 listener
    applied.

    F1b part 2: runs from the worker's main loop every CERT_CHECK_INTERVAL_SECONDS
    (~30s, bounded by ISSUED_CHECK_TIMEOUT ~30min and this tick's own
    CERT_RECHECK_TIME_BUDGET_SECONDS) — never inside a dispatched provisioning job's lock,
    per the pre-review (the DNS writer never holds customer credentials; this is a
    customer-account DescribeCertificate call, so it belongs here, not in run_dns_writer).
    Uses `assume_role_credentials_only`, not `authenticate_infrastructure` — the latter
    writes `is_cloud_authenticated`/`metadata` on every call, which a transient failure in
    this background poll must not be allowed to flip on the customer-facing row. A row
    past ISSUED_CHECK_TIMEOUT is marked FAILED without ever touching Environment.status —
    the environment stays ACTIVE on its path URL, and the row is retried the next time
    provision() runs cert_bootstrap.ensure_certificate. Every action here is additionally
    gated by `_cert_recheck_eligible` — see its docstring.
    """
    from api.cloud_providers.aws.authenticate import assume_role_credentials_only
    from api.common.envs.application import app_config
    from api.models.environment import Environment
    from api.models.infrastructure_certificate import InfrastructureCertificate
    from api.services import cert_bootstrap
    from api.services.infra_queue import InfraQueue
    from django.utils import timezone
    from shared.mode import is_dev_mode

    dev_mode = is_dev_mode(app_config.mode)
    deadline = time.monotonic() + CERT_RECHECK_TIME_BUDGET_SECONDS

    pending = InfrastructureCertificate.objects.filter(
        tls_status=InfrastructureCertificate.TLS_PENDING,
    ).select_related("infrastructure")

    for cert in pending:
        if time.monotonic() > deadline:
            logger.warning("TLS PENDING re-check hit its time budget; remaining rows deferred to next tick")
            break

        infra = cert.infrastructure
        env = Environment.objects.filter(infrastructure_id=infra.id).first()
        if not _cert_recheck_eligible(infra, env):
            continue

        timed_out = (
            cert.tls_requested_at is not None
            and timezone.now() - cert.tls_requested_at > cert_bootstrap.ISSUED_CHECK_TIMEOUT
        )
        if timed_out:
            InfrastructureCertificate.objects.filter(
                id=cert.id, tls_status=InfrastructureCertificate.TLS_PENDING,
            ).update(tls_status=InfrastructureCertificate.TLS_FAILED)
            logger.warning(
                f"TLS bootstrap timed out for infra {infra.id} after "
                f"{cert_bootstrap.ISSUED_CHECK_TIMEOUT}; marking FAILED (env stays ACTIVE on path URL)"
            )
            _publish_host_readiness(infra.id)
            continue

        if not cert.cert_arn:
            # R1 defense in depth: a null ARN should no longer happen (cert_arn is now
            # persisted immediately after RequestCertificate), but a row from before that
            # fix, or a bug, must still age out via the same timeout rather than stay
            # PENDING forever with nothing to check against ACM.
            continue

        try:
            credentials = assume_role_credentials_only(infra)
            region = (infra.metadata or {}).get("aws_region", "us-west-2")
            client = cert_bootstrap._acm_client(
                infra_is_mock=infra.is_mock, dev_mode=dev_mode, credentials=credentials, region=region,
            )
            status = client.describe_certificate(CertificateArn=cert.cert_arn)["Certificate"]["Status"]
        except Exception:
            logger.warning(f"TLS ISSUED re-check failed for infra {infra.id} (will retry next tick)", exc_info=True)
            continue

        if status == "ISSUED":
            updated = InfrastructureCertificate.objects.filter(
                id=cert.id, tls_status=InfrastructureCertificate.TLS_PENDING,
            ).update(tls_status=InfrastructureCertificate.TLS_ISSUED)
            if updated:
                logger.info(f"TLS certificate ISSUED for infra {infra.id}; re-enqueuing provision to apply 443")
                InfraQueue.enqueue_provision(str(infra.id))
                _publish_host_readiness(infra.id)
        elif status in ("FAILED", "VALIDATION_TIMED_OUT", "REVOKED"):
            InfrastructureCertificate.objects.filter(
                id=cert.id, tls_status=InfrastructureCertificate.TLS_PENDING,
            ).update(tls_status=InfrastructureCertificate.TLS_FAILED)
            logger.warning(f"ACM reports {status} for infra {infra.id} certificate; marking FAILED")
            _publish_host_readiness(infra.id)

    _reenqueue_issued_certs_missing_https_listener(deadline)
    _apply_eks_tls_for_issued_certs(deadline, dev_mode)


def _publish_host_readiness(infra_id) -> None:
    from api.services.host_readiness import publish_host_readiness
    publish_host_readiness(infra_id)


def _reenqueue_issued_certs_missing_https_listener(deadline):
    """Recovery for a lost enqueue_provision on the ISSUED transition above — e.g. the
    Redis dedup key InfraQueue.enqueue_provision checks was already held by an unrelated
    concurrent provision that committed before the certificate flipped to ISSUED, silently
    dropping that one enqueue attempt. ECS only: EKS applies TLS via the Ingress class
    patch, not `Environment.https_listener_arn`. Cheap to call every tick — a row whose
    listener is already applied never matches the filter again, and InfraQueue's own
    dedup key makes a redundant enqueue while one is already queued/running a no-op."""
    from api.models.environment import Environment
    from api.models.infrastructure_certificate import InfrastructureCertificate
    from api.services.infra_queue import InfraQueue
    from shared.enums.orchestrator import ComputeType

    issued = InfrastructureCertificate.objects.filter(
        tls_status=InfrastructureCertificate.TLS_ISSUED,
    ).select_related("infrastructure")

    for cert in issued:
        if time.monotonic() > deadline:
            logger.warning("TLS ISSUED re-enqueue sweep hit its time budget; remaining rows deferred to next tick")
            break

        infra = cert.infrastructure
        if infra.compute_type != ComputeType.ECS_FARGATE:
            continue

        env = Environment.objects.filter(infrastructure_id=infra.id).first()
        if not _cert_recheck_eligible(infra, env):
            continue
        if env.https_listener_arn:
            continue

        logger.info(f"TLS ISSUED for infra {infra.id} but https_listener_arn is unset; re-enqueuing provision")
        InfraQueue.enqueue_provision(str(infra.id))


# R3 (security review): bounded retry for a persistently failing EKS TLS patch attempt
# against a STABLE target cert_arn — mirrors cert_bootstrap.ISSUED_CHECK_TIMEOUT's own
# age-based cutoff for the PENDING-certificate loop, so an unreachable cluster or a k8s API
# throttle doesn't get a fresh AssumeRole + describe_cluster + patch attempt every ~30s
# tick forever.
EKS_TLS_PATCH_TIMEOUT = timedelta(minutes=30)


def _apply_eks_tls_for_issued_certs(deadline, dev_mode: bool):
    """EKS counterpart to _reenqueue_issued_certs_missing_https_listener above: patch the
    cluster's IngressClassParams once TLS is ISSUED, instead of re-enqueuing a terraform
    apply (EKS's 443 wiring is a k8s object patch, not a terraform module). Uses
    `assume_role_credentials_only`, same reasoning as the PENDING re-check loop — a
    transient failure here must not flip `is_cloud_authenticated`/`metadata` on the
    customer-facing row.

    R3: `eks_ingress_tls_ready` alone is not enough to decide "nothing to do" — a
    certificate re-issue (FAILED -> a fresh RequestCertificate, a new ARN) after this
    field was already True from a now-superseded cert must trigger a re-patch, or the
    Ingress class keeps serving a stale/deleted certificate ARN forever. `eks_ingress_tls_
    cert_arn` tracks which cert_arn eks_ingress_tls_ready actually reflects (or is
    currently being attempted against); a mismatch against the currently-ISSUED cert's own
    ARN means "needs (re-)patching," never a one-shot latch.
    """
    from api.cloud_providers.aws.authenticate import assume_role_credentials_only
    from api.models.environment import Environment
    from api.models.infrastructure_certificate import InfrastructureCertificate
    from api.services.eks_bootstrap import apply_eks_tls
    from api.services.host_readiness import publish_host_readiness
    from shared.enums.orchestrator import ComputeType

    issued = InfrastructureCertificate.objects.filter(
        tls_status=InfrastructureCertificate.TLS_ISSUED,
    ).select_related("infrastructure")

    for cert in issued:
        if time.monotonic() > deadline:
            logger.warning("EKS TLS patch sweep hit its time budget; remaining rows deferred to next tick")
            break

        infra = cert.infrastructure
        if infra.compute_type != ComputeType.EKS:
            continue

        env = Environment.objects.filter(infrastructure_id=infra.id).first()
        if not _cert_recheck_eligible(infra, env):
            continue
        if not env.cluster_arn or not cert.cert_arn:
            continue

        already_patched = env.eks_ingress_tls_ready and env.eks_ingress_tls_cert_arn == cert.cert_arn
        if already_patched:
            continue

        now = timezone.now()
        target_changed = env.eks_ingress_tls_cert_arn != cert.cert_arn
        if target_changed:
            # A fresh target (first attempt ever, or the cert was re-issued since the
            # last one we patched/attempted) — reset the attempt clock and drop the
            # ready flag immediately, so a stale True is never left mirrored out while a
            # re-patch is pending.
            Environment.objects.filter(id=env.id).update(
                eks_ingress_tls_ready=False, eks_ingress_tls_cert_arn=cert.cert_arn,
                eks_ingress_tls_patch_attempted_at=now,
            )
            env.eks_ingress_tls_cert_arn = cert.cert_arn
            env.eks_ingress_tls_patch_attempted_at = now
            publish_host_readiness(infra.id)
        elif (
            env.eks_ingress_tls_patch_attempted_at is not None
            and now - env.eks_ingress_tls_patch_attempted_at > EKS_TLS_PATCH_TIMEOUT
        ):
            logger.warning(
                f"EKS TLS patch for infra {infra.id} has been failing against cert "
                f"{cert.cert_arn} for over {EKS_TLS_PATCH_TIMEOUT}; giving up until the "
                "target certificate changes"
            )
            continue

        try:
            credentials = assume_role_credentials_only(infra)
            region = (infra.metadata or {}).get("aws_region", "us-west-2")
            cluster_name = env.cluster_arn.split("/")[-1]
            apply_eks_tls(
                infra, credentials=credentials, region=region, cluster_name=cluster_name,
                cert_arn=cert.cert_arn, infra_is_mock=infra.is_mock, dev_mode=dev_mode,
            )
        except Exception:
            logger.warning(f"EKS TLS patch failed for infra {infra.id} (will retry next tick)", exc_info=True)
            continue

        Environment.objects.filter(id=env.id).update(
            eks_ingress_tls_ready=True, eks_ingress_tls_patch_attempted_at=None,
        )
        logger.info(f"EKS IngressClassParams patched with TLS for infra {infra.id}")
        publish_host_readiness(infra.id)


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
# F1b part 2: how often the worker re-checks PENDING certificates for ISSUED/FAILED.
CERT_CHECK_INTERVAL_SECONDS = int(os.environ.get('INFRA_CERT_CHECK_INTERVAL_SECONDS', '30'))
# F1b part 3b: how often the worker re-validates VALIDATED custom domains' ownership TXT
# and sweeps expired PENDING claims. Slower than the TLS cert check — an authoritative DNS
# query per domain is heavier than a DescribeCertificate call, and losing ownership of a
# custom domain is a slow-moving condition (a customer transferring a domain away, letting
# a TXT record lapse), not one that needs sub-minute detection.
CUSTOM_DOMAIN_CHECK_INTERVAL_SECONDS = int(os.environ.get('INFRA_CUSTOM_DOMAIN_CHECK_INTERVAL_SECONDS', '300'))
# Hard ceiling dispatch will wait for the custom-domain check before giving up on this
# tick and moving on — well under CUSTOM_DOMAIN_CHECK_INTERVAL_SECONDS's own Redis lock
# TTL (~295s default), so a stalled check's lock has already expired by the time this
# fires, and well under it so dispatch itself is never meaningfully starved.
CUSTOM_DOMAIN_CHECK_HARD_TIMEOUT_SECONDS = int(os.environ.get('INFRA_CUSTOM_DOMAIN_CHECK_HARD_TIMEOUT_SECONDS', '60'))
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

    from api.models.environment import Environment
    from api.services.infra_queue import InfraQueue
    from api.services.notification import NotificationService
    from api.services.terraform_worker import _capped_error
    from django.db.models import Q
    from django.utils import timezone

    cutoff = timezone.now() - timedelta(seconds=stale_threshold_seconds)
    # A stale DB lock means a crashed run. A null DB lock is ambiguous: it's either a job still
    # waiting in the queue (its Redis dedup key is set) or one whose queue entry was lost (key
    # gone). The dedup key — not a timestamp — is the authoritative signal, so the null case is
    # filtered per-row below (updated_at is unreliable: save(update_fields=['status']) never bumps
    # it, so an ACTIVE->DESTROYING row would look aged the instant it's enqueued).
    stuck = Environment.objects.filter(
        status__in=['PROVISIONING', 'UPDATING', 'DESTROYING'],
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
            # An UPDATING environment that was live before this run still has real
            # resources serving traffic — park it back to ACTIVE, not ERROR. A stuck
            # DESTROYING run may have already torn down some resources, so it must not
            # be reported healthy; park it ERROR like any other abandoned run.
            if env.first_activated_at is not None and env.status != 'DESTROYING':
                park_status = 'ACTIVE'
                park_message = f"{env.status} update could not be recovered after {MAX_REAP_ATTEMPTS} attempts; environment returned to ACTIVE"
            else:
                park_status = 'ERROR'
                park_message = f"{env.status} abandoned after {MAX_REAP_ATTEMPTS} recovery attempts"
            logger.error(f"Reaper giving up on {infra_id} after {MAX_REAP_ATTEMPTS} attempts; parking in {park_status}")
            Environment.objects.filter(infrastructure_id=infra_id).update(
                status=park_status, locked_at=None, locked_by=None,
                error_message=_capped_error(park_message),
            )
            InfraQueue.release_lock(infra_id)
            InfraQueue.clear_reap_count(infra_id)
            try:
                infra = env.infrastructure
                notify = NotificationService.send_destroy_failure if env.status == 'DESTROYING' \
                    else NotificationService.send_provision_failure
                notify(str(infra.user_id), infra_id, infra.name,
                       f"{env.status.capitalize()} could not be recovered")
            except Exception:
                logger.exception(f"Failed to notify abandonment for {infra_id}")
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
        logger.exception(f"Failed to (re)publish infra.created for {infra.id}")


def _close_db():
    """Close only unusable/obsolete connections rather than all connections."""
    for conn in connections.all():
        conn.close_if_unusable_or_obsolete()


class Command(BaseCommand):
    help = 'Run the infrastructure provisioning worker'

    def handle(self, *args, **options):
        from api.models.environment import Environment
        from api.models.infrastructure import Infrastructure
        from api.services.infra_queue import InfraQueue
        from api.services.log_redaction import redact_provisioning_text
        from api.services.notification import NotificationService
        from api.services.terraform_worker import TerraformWorker, _capped_error
        from django.conf import settings
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
        # F1b part 3b (security review B1): a customer-controlled hostname's authoritative
        # DNS is an attacker-reachable surface. custom_domain_dns.py bounds a single
        # lookup to its own hard deadline, but running the whole tick inline on this
        # dispatch thread would still let any bug in that bound (or an OS-level stall
        # dnspython's own timeout can't fully guarantee against) block every
        # provision/destroy dispatch fleet-wide for as long as it stalls. One dedicated
        # worker thread, with dispatch bounded by CUSTOM_DOMAIN_CHECK_HARD_TIMEOUT_SECONDS
        # below, keeps dispatch responsive even if the check itself never returns.
        custom_domain_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='custom-domain-check')
        pending_futures: list[Future] = []

        logger.info(f"Infrastructure worker {worker_id} started "
                    f"(provision={MAX_PROVISION_WORKERS}, destroy={MAX_DESTROY_WORKERS})")

        # Re-enqueue stuck jobs — only one worker should do this at startup
        recovery_lock_key = "infra:worker:recovery_lock"
        from api.services.infra_queue import PROVISION_QUEUE
        from api.services.infra_queue import _redis as _get_redis
        r = _get_redis()
        if r.set(recovery_lock_key, worker_id, nx=True, ex=60):
            try:
                from api.services.infra_queue import DESTROY_QUEUE
                for env in Environment.objects.filter(status__in=['PENDING', 'PROVISIONING', 'UPDATING']).select_related('infrastructure'):
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

        def _notify_database_outcomes(infra, pending_dbs):
            """pending_dbs: {database_id (str): prior_status} snapshotted before this
            provision run. Fires one create/delete success/failure email per row that
            reached a terminal state — a single apply can resolve several at once."""
            if not pending_dbs:
                return
            from api.models.database import Database
            for db in Database.objects.filter(id__in=pending_dbs.keys()):
                was_delete = pending_dbs[str(db.id)] == 'DELETING'
                if db.status == 'DELETED':
                    NotificationService.send_database_delete_success(str(infra.user_id), str(infra.id), infra.name, db.name)
                elif db.status == 'ACTIVE':
                    NotificationService.send_database_create_success(str(infra.user_id), str(infra.id), infra.name, db.name)
                elif db.status == 'ERROR':
                    notify = NotificationService.send_database_delete_failure if was_delete \
                        else NotificationService.send_database_create_failure
                    notify(str(infra.user_id), str(infra.id), infra.name, db.error_message or 'Unknown error', db.name)

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

                from api.models.database import Database
                pending_dbs = {
                    str(db_id): db_status for db_id, db_status in Database.objects.filter(
                        environment__infrastructure_id=infra_id,
                        status__in=['PENDING', 'PROVISIONING', 'DELETING'],
                    ).values_list('id', 'status')
                }

                TerraformWorker.provision(infra_id)
                env = Environment.objects.get(infrastructure_id=infra_id)
                _notify_database_outcomes(infra, pending_dbs)
                # A run whose only purpose was reconciling database rows already gets its
                # own database_* email above — the generic "infrastructure ready" email
                # would be redundant noise on an environment that was already ACTIVE.
                if env.status == 'ACTIVE':
                    InfraQueue.clear_reap_count(infra_id)
                    if not pending_dbs:
                        if env.error_message:
                            NotificationService.send_provision_failure(str(infra.user_id), infra_id, infra.name, env.error_message)
                        else:
                            NotificationService.send_provision_success(str(infra.user_id), infra_id, infra.name)
                elif env.status == 'ERROR' and not pending_dbs:
                    NotificationService.send_provision_failure(str(infra.user_id), infra_id, infra.name, env.error_message or 'Unknown error')
            except Exception as e:
                logger.error(f"Provision failed for {infra_id}: {redact_provisioning_text(str(e)).text}",
                             exc_info=False)
                if infra:
                    try:
                        NotificationService.send_provision_failure(str(infra.user_id), infra_id, infra.name, _capped_error(str(e)))
                    except Exception:  # noqa: S110 - best-effort notification, must not mask the original failure
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
                    except Exception:
                        logger.exception(f"Failed to clean up Environment for {infra_id}")
                    return

                try:
                    env = Environment.objects.get(infrastructure_id=infra_id)
                    if env.status == 'DESTROYED':
                        if _refuse_hard_delete_for_live_dns(infra_id):
                            logger.error(
                                f"Refusing to delete DB records for {infra_id}: platform "
                                "DNS records are still live; leaving DESTROYED for the "
                                "next teardown pass to clear them"
                            )
                            return
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
                    if _refuse_hard_delete_for_live_dns(infra_id):
                        logger.error(
                            f"Refusing to delete Infrastructure {infra_id}: platform DNS "
                            "records are still live"
                        )
                        return
                    NotificationService.send_destroy_success(str(infra.user_id), infra_id, infra.name)
                    user_id = infra.user_id
                    with transaction.atomic():
                        infra.delete()
                    logger.info(f"Deleted DB records for {infra_id}")
                    _publish_infra_deleted(user_id, infra_id)
            except Exception as e:
                logger.error(f"Destroy failed for {infra_id}: {redact_provisioning_text(str(e)).text}",
                             exc_info=False)
                if infra:
                    try:
                        NotificationService.send_destroy_failure(str(infra.user_id), infra_id, infra.name, _capped_error(str(e)))
                    except Exception:  # noqa: S110 - best-effort notification, must not mask the original failure
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

        def run_custom_domain_checks():
            """Runs on custom_domain_pool's own dedicated thread — never on the dispatch
            thread — so a stalled authoritative-DNS lookup (or a bug in its deadline
            enforcement) can only block this one thread, not provision/destroy dispatch.
            See CUSTOM_DOMAIN_CHECK_HARD_TIMEOUT_SECONDS at the call site for the bound
            dispatch itself waits on."""
            from api.services.custom_domain_service import CustomDomainService
            try:
                service = CustomDomainService()
                service.sweep_expired_claims()
                service.revalidate_validated_domains()
                service.sweep_stuck_disabling()
            finally:
                _close_db()

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
                logger.exception(f"Failed to submit provision job for {infra_id}")
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
                logger.exception(f"Failed to submit destroy job for {infra_id}")
                raise

        reap_lock_key = "infra:worker:reap_lock"
        cert_check_lock_key = "infra:worker:cert_check_lock"
        custom_domain_check_lock_key = "infra:worker:custom_domain_check_lock"
        provision_counter = 0
        last_reap = time.monotonic()
        last_cert_check = time.monotonic()
        last_custom_domain_check = time.monotonic()
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
                            logger.exception("Reaper sweep failed")

                # F1b part 2: advance PENDING TLS certificates toward ISSUED/FAILED. Same
                # fleet-wide rate limit as the reaper above — one worker per interval, never
                # inside a dispatched job's lock.
                if time.monotonic() - last_cert_check >= CERT_CHECK_INTERVAL_SECONDS:
                    last_cert_check = time.monotonic()
                    if r.set(cert_check_lock_key, worker_id, nx=True, ex=max(CERT_CHECK_INTERVAL_SECONDS - 5, 10)):
                        try:
                            check_pending_certificates()
                        except Exception:
                            logger.exception("TLS certificate re-check sweep failed")

                # F1b part 3b: re-validate VALIDATED custom domains' ownership TXT and
                # sweep expired PENDING claims. Same fleet-wide rate limit as the checks
                # above — one worker per interval, never inside a dispatched job's lock
                # (each domain's own AssumeRole + DNS/ACM calls are independent of any
                # infra's provisioning lock). Submitted to its own thread with a hard
                # wait ceiling (security review B1) rather than run inline — see
                # run_custom_domain_checks's docstring.
                if time.monotonic() - last_custom_domain_check >= CUSTOM_DOMAIN_CHECK_INTERVAL_SECONDS:
                    last_custom_domain_check = time.monotonic()
                    if r.set(custom_domain_check_lock_key, worker_id,
                             nx=True, ex=max(CUSTOM_DOMAIN_CHECK_INTERVAL_SECONDS - 5, 10)):
                        future = custom_domain_pool.submit(run_custom_domain_checks)
                        try:
                            future.result(timeout=CUSTOM_DOMAIN_CHECK_HARD_TIMEOUT_SECONDS)
                        except concurrent.futures.TimeoutError:
                            logger.error(
                                "custom-domain re-validation/sweep exceeded its %ss hard "
                                "timeout; dispatch continues — the stuck call keeps "
                                "running on its own thread and the Redis lock above "
                                "bounds how soon another worker retries",
                                CUSTOM_DOMAIN_CHECK_HARD_TIMEOUT_SECONDS,
                            )
                        except Exception:
                            logger.exception("custom-domain re-validation/sweep failed")

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
                    logger.exception("Worker loop error")
                _close_db()

        logger.info("Waiting for in-flight jobs to complete...")
        provision_pool.shutdown(wait=False)
        destroy_pool.shutdown(wait=False)
        custom_domain_pool.shutdown(wait=False)

        _, not_done = concurrent.futures.wait(pending_futures, timeout=SHUTDOWN_TIMEOUT)
        if not_done:
            logger.warning(f"Shutdown timeout reached, {len(not_done)} job(s) may be interrupted")
        logger.info(f"Worker {worker_id} stopped")


def _log_future_exception(future: Future, infra_id: str, op: str):
    exc = future.exception()
    if exc:
        logger.error(f"Unhandled exception in {op} task for {infra_id}: {exc}", exc_info=exc)
