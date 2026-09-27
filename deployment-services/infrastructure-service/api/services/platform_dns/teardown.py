"""Worker-side (no DNS credentials) half of DNS teardown.

The provisioning/destroy worker never holds PLATFORM_DNS_* credentials and must not import
route53_client's real-client path — it only ever (a) sets the monotonic teardown marker,
(b) asks the writer to reconcile, and (c) reads the PlatformDnsRecord ledger, a plain
Postgres table, to find out whether the writer finished. This keeps DNS isolation intact
even though the worker and the writer share one Django project.
"""
import logging
import time

from api.models.infrastructure import Infrastructure
from api.models.platform_dns_record import PlatformDnsRecord

from .producer import request_dns_reconcile

logger = logging.getLogger(__name__)

DEFAULT_TEARDOWN_TIMEOUT_SECONDS = 20
DEFAULT_TEARDOWN_POLL_INTERVAL_SECONDS = 1


class DnsTeardownPending(RuntimeError):
    """The writer has not confirmed teardown within the wait window.

    Callers on the destroy path must let this propagate rather than continue: the caller
    (TerraformWorker.destroy) is expected to leave Environment.status as DESTROYING so the
    reaper re-enqueues and retries later, instead of proceeding to AssumeRole/terraform
    destroy while a stale edge/wildcard record might still exist.
    """


def request_and_await_dns_teardown(
    infrastructure_id,
    *,
    timeout_seconds: int = DEFAULT_TEARDOWN_TIMEOUT_SECONDS,
    poll_interval_seconds: int = DEFAULT_TEARDOWN_POLL_INTERVAL_SECONDS,
) -> None:
    infra_id = str(infrastructure_id)

    infra = Infrastructure.objects.filter(id=infra_id).first()
    if infra is not None:
        infra.mark_dns_teardown_requested()

    # coalesce=False: this call must always publish, even if an unrelated routine reconcile
    # for the same infra is still inside its debounce window (see producer.py).
    request_dns_reconcile(infra_id, coalesce=False)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not PlatformDnsRecord.objects.filter(infrastructure_id=infra_id).exists():
            logger.info("platform DNS teardown confirmed for %s", infra_id)
            return
        time.sleep(poll_interval_seconds)

    raise DnsTeardownPending(
        f"platform DNS teardown not confirmed for {infra_id} within {timeout_seconds}s"
    )


def has_live_dns_state(infrastructure_id) -> bool:
    """True if anything still gates a hard delete of this infrastructure: a live ledger
    row, or a cert row that ever started requesting a certificate. Deliberately never
    consults Environment.status — see desired_state.py's module docstring."""
    from api.models.infrastructure_certificate import InfrastructureCertificate

    infra_id = str(infrastructure_id)
    if PlatformDnsRecord.objects.filter(infrastructure_id=infra_id).exists():
        return True
    return InfrastructureCertificate.objects.filter(
        infrastructure_id=infra_id, tls_requested_at__isnull=False,
    ).exists()


def delete_acm_certificate_after_listener_removed(infrastructure_id) -> None:
    """Hook for F1b part 2. Order required by the security pre-review: the 443 listener
    (and ALB) must be gone before acm:DeleteCertificate — a cert still referenced by a
    listener fails with ResourceInUse — and the validation CNAME must be deleted
    unconditionally afterward regardless of whether this call succeeds. Not implemented in
    part 1: no code path issues an ACM certificate yet, so there is nothing to delete."""
    return
