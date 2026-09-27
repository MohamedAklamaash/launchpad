"""Customer-account ACM certificate bootstrap — F1b part 2 (TLS activation).

Called from TerraformWorker._save_outputs, after a successful apply has already stamped
Environment.first_activated_at and committed — so every call here runs under the
dispatched provisioning job's DB lock (see run_worker.py's LockHeartbeat), never on its
own thread, and only once the environment has actually gone ACTIVE at least once. This
module never raises out to its caller: a certificate problem must never fail an otherwise
good apply. `tls_status` on InfrastructureCertificate is the observable outcome.

Gate order:
  1. `infra.policy_version` below MIN_POLICY_VERSION_FOR_TLS (the version that added the
     acm:* grants) -> tls_status = POLICY_STALE, no ACM call at all. The policy-refresh
     callback re-enqueues provision once the customer applies the newer policy (see
     api/views/onboarding.py's refresh handler), which re-runs this gate.
  2. A row already ISSUED, or PENDING and still inside its poll window, is a no-op here —
     the periodic re-check in run_worker.py owns the PENDING -> ISSUED/FAILED transition
     without holding this job's lock.
  3. A row FAILED (timed out on a previous attempt) gets its old certificate best-effort
     deleted, then falls through to a fresh request — same as no row at all.

Certificate naming is `*.{dns_label}.{PLATFORM_BASE_DOMAIN}` only — never the bare
`{dns_label}.{base}` — reusing api.services.platform_dns.naming so the exact same
allow-list the DNS writer enforces on the resulting validation record also bounds what
this module will ever accept back from ACM.
"""
import hashlib
import logging
import time
from datetime import timedelta

from api.models.infrastructure_certificate import InfrastructureCertificate
from api.services.platform_dns import naming
from api.services.platform_dns.producer import request_dns_reconcile
from api.services.platform_dns.route53_client import MockRealMismatch
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Lowest Infrastructure.policy_version carrying the acm:* grants added in policy.json v4.
# Not derived from iam_policy.required_version_for(compute_type) — that answers "does this
# compute_type's rendered document differ from an earlier version" for *any* grant, which
# is the wrong question here; this module cares specifically about ACM.
MIN_POLICY_VERSION_FOR_TLS = 4

CERT_TAG_KEY = "ManagedBy"
CERT_TAG_VALUE = "launchpad"

_IDEMPOTENCY_TOKEN_MAX_LEN = 32  # ACM's own cap on IdempotencyToken length.

# Bounded poll for the DNS ResourceRecord right after RequestCertificate — ACM populates
# DomainValidationOptions[].ResourceRecord within seconds in practice; ~2 minutes is a
# generous bound before giving up on this attempt (the row is left PENDING and either a
# later provision or the periodic re-check will retry the describe, not the request).
_RESOURCE_RECORD_POLL_ATTEMPTS = 12
_RESOURCE_RECORD_POLL_INTERVAL_SECONDS = 10

# Bound for the ISSUED re-check loop (run from run_worker.py's periodic tick, ~30s
# cadence): past this, a still-PENDING certificate is marked FAILED rather than polled
# forever. The environment stays ACTIVE on its path URL regardless.
ISSUED_CHECK_TIMEOUT = timedelta(minutes=30)


class CertificateShapeError(ValueError):
    """ACM returned something outside the shapes this module (and the DNS writer) accept."""


def _idempotency_token(infra_id, requested_at) -> str:
    """Bound to this attempt's tls_requested_at, not just infra_id — a constant token
    would hand a retry (after the previous certificate was deleted, e.g. on a
    CertificateShapeError or a FAILED timeout) back the just-deleted ARN, since ACM's
    IdempotencyToken dedup window is ~1h. tls_requested_at advances monotonically on every
    attempt (see _ensure_certificate), so this is unique per attempt while still being a
    stable within-attempt backstop against a lost RequestCertificate response."""
    return hashlib.sha256(f"{infra_id}:{requested_at.isoformat()}".encode()).hexdigest()[:_IDEMPOTENCY_TOKEN_MAX_LEN]


def _base_domain() -> str:
    return settings.PLATFORM_BASE_DOMAIN


def _acm_client(*, infra_is_mock: bool, dev_mode: bool, credentials: dict, region: str):
    """Fail-closed mock/real gate, mirroring route53_client.get_route53_client exactly:
    both mismatches raise. By construction this should never actually mismatch — callers
    only reach here from TerraformWorker.provision()'s already-gated real/mock branches —
    but the check is cheap and a divergence here is exactly the kind of bug that would
    otherwise silently issue a real certificate in dev mode."""
    if infra_is_mock and not dev_mode:
        raise MockRealMismatch("refusing ACM cert bootstrap for a mock infrastructure outside dev mode")
    if dev_mode and not infra_is_mock:
        raise MockRealMismatch("refusing real ACM cert bootstrap against a mock infrastructure check inside dev mode")

    if dev_mode:
        return _shared_fake_acm()

    import boto3
    return boto3.client(
        "acm",
        region_name=region,
        aws_access_key_id=credentials.get("aws_access_key_id"),
        aws_secret_access_key=credentials.get("aws_secret_access_key"),
        aws_session_token=credentials.get("aws_session_token"),
    )


def ensure_certificate(infra, *, credentials: dict, region: str, infra_is_mock: bool, dev_mode: bool) -> None:
    """Idempotent. Safe to call on every successful apply — a no-op once ISSUED or while a
    request is already PENDING within its poll window. Never raises."""
    try:
        _ensure_certificate(infra, credentials=credentials, region=region,
                             infra_is_mock=infra_is_mock, dev_mode=dev_mode)
    except Exception:
        logger.exception("ACM cert bootstrap failed for infrastructure %s (non-fatal)", infra.id)


def _ensure_certificate(infra, *, credentials: dict, region: str, infra_is_mock: bool, dev_mode: bool) -> None:
    if not infra.dns_label:
        logger.warning("skipping TLS bootstrap for %s: no dns_label minted yet", infra.id)
        return

    cert = InfrastructureCertificate.objects.filter(infrastructure_id=infra.id).first()

    if infra.policy_version is None or infra.policy_version < MIN_POLICY_VERSION_FOR_TLS:
        _mark_policy_stale(infra, cert)
        return

    if cert is not None and cert.tls_status in (
        InfrastructureCertificate.TLS_ISSUED, InfrastructureCertificate.TLS_PENDING,
    ):
        return

    label = infra.dns_label
    base_domain = _base_domain()
    domain_name = naming.wildcard_record_name(label, base_domain)

    client = _acm_client(infra_is_mock=infra_is_mock, dev_mode=dev_mode, credentials=credentials, region=region)

    if cert is not None and cert.tls_status == InfrastructureCertificate.TLS_FAILED and cert.cert_arn:
        _best_effort_delete(client, cert.cert_arn)

    if cert is None:
        cert = InfrastructureCertificate(infrastructure=infra)

    # Monotonic, written BEFORE RequestCertificate — moves forward on every attempt
    # (first request or a post-timeout retry) and is never cleared back to null, matching
    # the model's documented invariant.
    requested_at = timezone.now()
    cert.tls_requested_at = requested_at
    cert.tls_status = InfrastructureCertificate.TLS_PENDING
    cert.cert_arn = None
    cert.validation_name = None
    cert.validation_value = None
    cert.save()

    cert_arn = _reuse_or_request_certificate(client, domain_name, infra.id, requested_at)

    # Saved immediately, before the poll below — a timeout/CertificateShapeError/
    # AccessDenied/crash during the poll must never leave a certificate that exists in the
    # customer's account with no ARN recorded anywhere in our DB: the periodic re-check
    # (run_worker.py) excludes null-ARN rows and can't advance a row it can't identify, and
    # ensure_certificate itself no-ops on PENDING, so a null ARN here would orphan the
    # certificate until the tagged-reuse scan happens to find it on some future call.
    cert.cert_arn = cert_arn
    cert.save(update_fields=["cert_arn", "updated_at"])

    try:
        record = _poll_for_resource_record(client, cert_arn, domain_name, label, base_domain)
    except CertificateShapeError:
        # Not a transient condition — ACM will never hand back a different DomainName or a
        # differently-shaped validation record for the same certificate on a later poll.
        # Delete it now rather than leaving it PENDING for 30 minutes only to fail anyway.
        logger.exception(
            "ACM certificate %s failed shape validation for infra %s; deleting and marking FAILED",
            cert_arn, infra.id,
        )
        _best_effort_delete(client, cert_arn)
        InfrastructureCertificate.objects.filter(id=cert.id).update(
            tls_status=InfrastructureCertificate.TLS_FAILED,
            cert_arn=None, validation_name=None, validation_value=None,
        )
        return

    cert.validation_name = record["Name"]
    cert.validation_value = record["Value"]
    cert.save(update_fields=["validation_name", "validation_value", "updated_at"])

    try:
        request_dns_reconcile(str(infra.id))
    except Exception:
        logger.warning(
            "platform DNS reconcile request failed after cert bootstrap for %s (non-fatal)",
            infra.id, exc_info=True,
        )


def _mark_policy_stale(infra, cert) -> None:
    if cert is not None and cert.tls_status in (
        InfrastructureCertificate.TLS_ISSUED, InfrastructureCertificate.TLS_POLICY_STALE,
    ):
        return
    if cert is None:
        cert = InfrastructureCertificate(infrastructure=infra)
    cert.tls_status = InfrastructureCertificate.TLS_POLICY_STALE
    cert.save()
    logger.info(
        "TLS bootstrap skipped for %s: policy_version %r < required %d",
        infra.id, infra.policy_version, MIN_POLICY_VERSION_FOR_TLS,
    )


def maybe_reenqueue_after_policy_refresh(infra_id) -> None:
    """R4: a POLICY_STALE certificate never re-evaluates itself on a timer — without this,
    a customer who refreshes their policy (onboarding or policy-refresh callback) stays
    stuck in POLICY_STALE until some unrelated trigger happens to re-provision. Call after
    Infrastructure.policy_version is written in either callback. Re-reads current DB state
    rather than trusting the caller's local value, so it's correct regardless of whether
    this particular call actually advanced anything. A no-op unless the environment is
    already ACTIVE — the ordinary onboarding path (env still PENDING) never enqueues here;
    InfraQueue.enqueue_provision's own Redis dedup makes this safe to call on every
    callback, not just the one that crossed the threshold.
    """
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.services.infra_queue import InfraQueue

    infra = Infrastructure.objects.filter(id=infra_id).first()
    if infra is None or infra.policy_version is None or infra.policy_version < MIN_POLICY_VERSION_FOR_TLS:
        return
    if infra.dns_teardown_requested_at is not None or infra.exited_at is not None:
        return
    if not Environment.objects.filter(infrastructure_id=infra_id, status='ACTIVE').exists():
        return
    InfraQueue.enqueue_provision(str(infra_id))


def _best_effort_delete(client, cert_arn: str) -> None:
    try:
        client.delete_certificate(CertificateArn=cert_arn)
    except Exception:
        logger.warning("best-effort ACM DeleteCertificate failed for %s (retry path)", cert_arn, exc_info=True)


def _reuse_or_request_certificate(client, domain_name: str, infra_id, requested_at) -> str:
    reused = _find_reusable_certificate(client, domain_name)
    if reused is not None:
        return reused

    response = client.request_certificate(
        DomainName=domain_name,
        ValidationMethod="DNS",
        IdempotencyToken=_idempotency_token(infra_id, requested_at),
        Tags=[{"Key": CERT_TAG_KEY, "Value": CERT_TAG_VALUE}],
    )
    return response["CertificateArn"]


def _find_reusable_certificate(client, domain_name: str) -> str | None:
    """List + tag filter, per the pre-review: only a certificate for this exact domain
    name, tagged ManagedBy=launchpad, AND actually issued by ACM (Type=AMAZON_ISSUED) is
    eligible — a customer's own unrelated wildcard certificate for the same name
    (vanishingly unlikely given dns_label's entropy, but not impossible) is never adopted,
    and neither is an IMPORTED or PRIVATE certificate that happens to carry the same tag
    (Launchpad never imports or issues private certificates, so a match there is
    definitely not one of ours). Follows NextToken — list_certificates is paginated and an
    account with enough certificates could otherwise silently miss the one we want."""
    next_token = None
    while True:
        kwargs = {"CertificateStatuses": ["PENDING_VALIDATION", "ISSUED"]}
        if next_token:
            kwargs["NextToken"] = next_token
        response = client.list_certificates(**kwargs)
        for summary in response.get("CertificateSummaryList", []):
            if summary.get("DomainName") != domain_name:
                continue
            if summary.get("Type", "AMAZON_ISSUED") != "AMAZON_ISSUED":
                continue
            arn = summary["CertificateArn"]
            tags_response = client.list_tags_for_certificate(CertificateArn=arn)
            tags = {tag["Key"]: tag["Value"] for tag in tags_response.get("Tags", [])}
            if tags.get(CERT_TAG_KEY) == CERT_TAG_VALUE:
                return arn
        next_token = response.get("NextToken")
        if not next_token:
            return None


def _poll_for_resource_record(client, cert_arn: str, expected_domain_name: str, label: str, base_domain: str) -> dict:
    for attempt in range(_RESOURCE_RECORD_POLL_ATTEMPTS):
        certificate = client.describe_certificate(CertificateArn=cert_arn)["Certificate"]
        if certificate.get("DomainName") != expected_domain_name:
            raise CertificateShapeError(
                f"ACM cert {cert_arn} has DomainName {certificate.get('DomainName')!r}, "
                f"expected {expected_domain_name!r}"
            )
        for option in certificate.get("DomainValidationOptions", []):
            record = option.get("ResourceRecord")
            if record:
                _assert_resource_record_shape(record, label, base_domain)
                return record
        if attempt + 1 < _RESOURCE_RECORD_POLL_ATTEMPTS:
            time.sleep(_RESOURCE_RECORD_POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"ACM cert {cert_arn} produced no DNS ResourceRecord after "
        f"{_RESOURCE_RECORD_POLL_ATTEMPTS} attempts"
    )


def _assert_resource_record_shape(record: dict, label: str, base_domain: str) -> None:
    if record.get("Type") != "CNAME":
        raise CertificateShapeError(f"unexpected ACM validation record type {record.get('Type')!r}")
    name = naming.normalize_route53_name(record.get("Name", ""))
    value = record.get("Value", "")
    try:
        naming.assert_owned_record_name(name, label, base_domain, kind="validation")
        naming.assert_valid_validation_value(value)
    except naming.InvalidDnsRecordError as exc:
        raise CertificateShapeError(str(exc)) from exc


# ── mock ACM double ──────────────────────────────────────────────────────────────

_fake_acm_singleton = None


def _shared_fake_acm():
    global _fake_acm_singleton
    if _fake_acm_singleton is None:
        _fake_acm_singleton = FakeAcmClient()
    return _fake_acm_singleton


def reset_fake_acm_for_tests() -> None:
    global _fake_acm_singleton
    _fake_acm_singleton = None


class FakeAcmClient:
    """In-memory ACM double for is_mock/dev cert bootstrap, mirroring
    route53_client.FakeRoute53Zone: process-local, on-the-wire shapes (trailing dots,
    CNAME validation records), just enough of the real API surface for this module.

    Simulates issuance after a short, bounded number of describe_certificate calls so a
    dev-mode reconcile loop (request -> poll -> ISSUED -> host URL) runs end-to-end
    without a real ACM account. Does not model ACM's real ~1h IdempotencyToken TTL —
    dedup here is for the lifetime of the process, a documented mock simplification.
    """

    ISSUE_AFTER_DESCRIBE_CALLS = 2

    def __init__(self):
        self._certs: dict[str, dict] = {}

    def request_certificate(self, DomainName, ValidationMethod, IdempotencyToken=None, Tags=None, **_kwargs):
        if IdempotencyToken:
            for arn, cert in self._certs.items():
                if cert["domain_name"] == DomainName and cert.get("idempotency_token") == IdempotencyToken:
                    return {"CertificateArn": arn}

        import secrets
        import uuid
        arn = f"arn:aws:acm:mock-region:000000000000:certificate/{uuid.uuid4()}"
        base = DomainName.removeprefix("*.")
        self._certs[arn] = {
            "domain_name": DomainName,
            "status": "PENDING_VALIDATION",
            "idempotency_token": IdempotencyToken,
            "tags": {tag["Key"]: tag["Value"] for tag in (Tags or [])},
            "resource_record": {
                "Name": f"_{secrets.token_hex(16)}.{base}.",
                "Type": "CNAME",
                "Value": f"_{secrets.token_hex(16)}.{secrets.token_hex(4)}.acm-validations.aws.",
            },
            "describe_calls": 0,
        }
        return {"CertificateArn": arn}

    def describe_certificate(self, CertificateArn):
        cert = self._certs[CertificateArn]
        cert["describe_calls"] += 1
        if cert["describe_calls"] >= self.ISSUE_AFTER_DESCRIBE_CALLS:
            cert["status"] = "ISSUED"
        return {
            "Certificate": {
                "CertificateArn": CertificateArn,
                "DomainName": cert["domain_name"],
                "Status": cert["status"],
                "DomainValidationOptions": [{
                    "DomainName": cert["domain_name"],
                    "ResourceRecord": cert["resource_record"],
                }],
            }
        }

    def list_certificates(self, CertificateStatuses=None, NextToken=None, **_kwargs):
        statuses = set(CertificateStatuses or [])
        return {
            "CertificateSummaryList": [
                {"CertificateArn": arn, "DomainName": cert["domain_name"], "Type": "AMAZON_ISSUED"}
                for arn, cert in self._certs.items()
                if not statuses or cert["status"] in statuses
            ]
        }

    def list_tags_for_certificate(self, CertificateArn):
        cert = self._certs[CertificateArn]
        return {"Tags": [{"Key": k, "Value": v} for k, v in cert["tags"].items()]}

    def add_tags_to_certificate(self, CertificateArn, Tags):
        self._certs[CertificateArn]["tags"].update({tag["Key"]: tag["Value"] for tag in Tags})

    def delete_certificate(self, CertificateArn):
        if CertificateArn not in self._certs:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "certificate not found"}},
                "DeleteCertificate",
            )
        del self._certs[CertificateArn]
