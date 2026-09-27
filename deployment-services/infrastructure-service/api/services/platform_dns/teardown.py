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
    """True if a hard delete of this infrastructure must be refused: a live
    PlatformDnsRecord ledger row. Deliberately never consults Environment.status — see
    desired_state.py's module docstring.

    Deliberately does NOT also check InfrastructureCertificate.tls_requested_at.
    tls_requested_at is monotonic (set once, never cleared, by design — see that model's
    docstring), so treating it as a delete-blocking condition would make any infrastructure
    that ever requested a certificate permanently undeletable, even long after teardown
    completed and its ledger rows are gone. tls_requested_at is a signal for *running*
    teardown (part 2's job), not a reason to refuse one that already succeeded.
    """
    infra_id = str(infrastructure_id)
    return PlatformDnsRecord.objects.filter(infrastructure_id=infra_id).exists()


_DELETE_CERT_RETRY_ATTEMPTS = 6
_DELETE_CERT_RETRY_INTERVAL_SECONDS = 10


def delete_acm_certificate_after_listener_removed(
    infrastructure_id, *, credentials: dict, region: str, infra_is_mock: bool = False, dev_mode: bool = False,
) -> None:
    """F1b part 2. Best-effort acm:DeleteCertificate for this infra's InfrastructureCertificate
    row, called from TerraformWorker.destroy() only after terraform destroy has already
    removed the 443 listener and ALB. `credentials` here are the customer's AssumeRole
    credentials already held by the destroy path, not a PLATFORM_DNS_* credential; this
    module's "no DNS credentials" guarantee is about the platform zone, not the customer's
    own ACM API — `infra_is_mock`/`dev_mode` are threaded through to
    cert_bootstrap._acm_client purely so this call goes through the same fail-closed
    mock/real gate as every other ACM call in this feature, not a raw unconditional boto3
    client.

    A certificate ACM still considers referenced by a listener fails with
    ResourceInUseException — retried with a short backoff (up to
    _DELETE_CERT_RETRY_ATTEMPTS x _DELETE_CERT_RETRY_INTERVAL_SECONDS, ~1 minute total) to
    absorb the propagation delay between the listener actually being gone at the API level
    and ACM's own internal state catching up. Any other error is not retried here.

    Deliberately never raises: destroy() must proceed to DESTROYED regardless of whether
    ACM cleanup succeeds. A customer who deleted the deployment role, or a certificate ACM
    still refuses to delete after every retry, is logged at ERROR with the ARN (an
    orphaned certificate left in the customer's own account — annoying, not a cross-tenant
    risk) since there is no automatic follow-up: the InfrastructureCertificate row is
    cascade-deleted with the Infrastructure row once destroy() finishes, so this is the
    last point anything in Launchpad will ever look at that ARN again.

    The validation CNAME's removal does NOT depend on this call succeeding or even
    running: compute_desired_state already returns an empty desired state for the whole
    infra (wildcard, edge, AND validation) the moment dns_teardown_requested_at is set,
    and that reconcile already ran, synchronously awaited, before this function is ever
    called (see request_and_await_dns_teardown in TerraformWorker.destroy) — a strict
    superset of "validation CNAME deleted unconditionally, never gated on the cert
    delete" with a *shorter* window than a two-phase teardown would leave.
    """
    import time

    from api.models.infrastructure_certificate import InfrastructureCertificate
    from api.services.cert_bootstrap import _acm_client

    cert = InfrastructureCertificate.objects.filter(infrastructure_id=infrastructure_id).first()
    if cert is None or not cert.cert_arn:
        return

    try:
        client = _acm_client(
            infra_is_mock=infra_is_mock, dev_mode=dev_mode, credentials=credentials, region=region,
        )
    except Exception:
        logger.exception(
            "ACM client construction failed for infra %s cert %s; certificate left in the "
            "customer's account", infrastructure_id, cert.cert_arn,
        )
        return

    from botocore.exceptions import ClientError

    for attempt in range(_DELETE_CERT_RETRY_ATTEMPTS):
        try:
            client.delete_certificate(CertificateArn=cert.cert_arn)
            logger.info("deleted ACM certificate %s for infra %s", cert.cert_arn, infrastructure_id)
            return
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "ResourceInUseException" or attempt + 1 >= _DELETE_CERT_RETRY_ATTEMPTS:
                logger.exception(
                    "ACM DeleteCertificate failed for infra %s cert %s after %d attempt(s): %s "
                    "(certificate left in the customer's account)",
                    infrastructure_id, cert.cert_arn, attempt + 1, code,
                )
                return
            logger.warning(
                "ACM DeleteCertificate for infra %s cert %s got ResourceInUse (attempt %d/%d); retrying",
                infrastructure_id, cert.cert_arn, attempt + 1, _DELETE_CERT_RETRY_ATTEMPTS,
            )
            time.sleep(_DELETE_CERT_RETRY_INTERVAL_SECONDS)
        except Exception:
            logger.exception(
                "ACM DeleteCertificate failed for infra %s cert %s (certificate left in the "
                "customer's account)", infrastructure_id, cert.cert_arn,
            )
            return
