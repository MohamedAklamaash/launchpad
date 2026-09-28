"""Per-customer-domain ACM certificate lifecycle (F1b part 3b).

Distinct from `cert_bootstrap.py`'s per-infrastructure wildcard certificate
(`*.{dns_label}.{PLATFORM_BASE_DOMAIN}`, issued once per infra and DNS-validated through
the platform's own zone): this module issues one certificate per claimed custom hostname,
DNS-validated by the *customer's* own DNS (they publish the CNAME ACM hands back, alongside
the ownership TXT and the `edge.` CNAME — see custom_domain_dns.py and
api/services/custom_domain_service.py).

Reuses cert_bootstrap's mock/real client factory and fake ACM double rather than
duplicating them — both modules share one AWS account's ACM certificate list, and the
existing `ManagedBy=launchpad` tagging convention (policy.json v4's ACM grants are already
conditioned on it) applies identically to a per-domain cert.
"""
import hashlib
import logging
import time

from api.services.cert_bootstrap import CERT_TAG_KEY, CERT_TAG_VALUE, _acm_client
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_IDEMPOTENCY_TOKEN_MAX_LEN = 32

# Bounded, short: this runs synchronously inside the claim HTTP request (behind the
# gateway's proxy timeout), unlike cert_bootstrap's own ~2 minute poll for the wildcard
# cert, which runs inside a background provisioning job. ACM populates
# DomainValidationOptions[].ResourceRecord within seconds in practice (same observation
# cert_bootstrap.py makes); a claim that doesn't get the record in this short window still
# succeeds — CustomDomainService backfills the record on the next read (see
# describe_validation_record) rather than blocking the request further.
_CLAIM_POLL_ATTEMPTS = 5
_CLAIM_POLL_INTERVAL_SECONDS = 1


class CertificateShapeError(ValueError):
    """ACM returned something outside the shape this module accepts for the domain it was
    asked to issue a certificate for."""


def _idempotency_token(domain_id, requested_at) -> str:
    return hashlib.sha256(f"{domain_id}:{requested_at.isoformat()}".encode()).hexdigest()[:_IDEMPOTENCY_TOKEN_MAX_LEN]


def request_certificate(hostname: str, domain_id, requested_at, *, credentials: dict, region: str,
                         infra_is_mock: bool, dev_mode: bool) -> str:
    """Requests a DNS-validated ACM certificate for the exact hostname (never a wildcard —
    a per-domain cert has no reason to cover siblings the customer didn't claim). Returns
    the certificate ARN. Never reuses an existing certificate the way cert_bootstrap's
    wildcard flow does: a per-domain claim is rare enough, and a stray same-name customer
    certificate sharing our tag is exactly as unlikely here as it is there, but adopting
    the wrong one for a customer-facing hostname is a worse outcome than requesting an
    extra certificate ACM will let us delete for free."""
    client = _acm_client(infra_is_mock=infra_is_mock, dev_mode=dev_mode, credentials=credentials, region=region)
    response = client.request_certificate(
        DomainName=hostname,
        ValidationMethod="DNS",
        IdempotencyToken=_idempotency_token(domain_id, requested_at),
        Tags=[{"Key": CERT_TAG_KEY, "Value": CERT_TAG_VALUE}],
    )
    return response["CertificateArn"]


def _describe(client, cert_arn: str) -> dict:
    return client.describe_certificate(CertificateArn=cert_arn)["Certificate"]


def _extract_validation_record(certificate: dict, expected_domain: str) -> dict | None:
    if certificate.get("DomainName") != expected_domain:
        raise CertificateShapeError(
            f"ACM cert {certificate.get('CertificateArn')} has DomainName "
            f"{certificate.get('DomainName')!r}, expected {expected_domain!r}"
        )
    for option in certificate.get("DomainValidationOptions", []):
        record = option.get("ResourceRecord")
        if record and record.get("Type") == "CNAME" and record.get("Name") and record.get("Value"):
            return {"name": record["Name"], "value": record["Value"], "type": "CNAME"}
    return None


def poll_for_validation_record(cert_arn: str, hostname: str, *, credentials: dict, region: str,
                                infra_is_mock: bool, dev_mode: bool) -> dict | None:
    """A short, bounded attempt to fetch the CNAME record ACM wants for validation. Returns
    None (not an error) if ACM hasn't populated it yet within the bound — see the module
    docstring on why this stays short. CertificateShapeError is not swallowed: a
    certificate ACM issued for a different DomainName than we asked for is a real bug, not
    a "try again later" condition."""
    client = _acm_client(infra_is_mock=infra_is_mock, dev_mode=dev_mode, credentials=credentials, region=region)
    for attempt in range(_CLAIM_POLL_ATTEMPTS):
        record = _extract_validation_record(_describe(client, cert_arn), hostname)
        if record is not None:
            return record
        if attempt + 1 < _CLAIM_POLL_ATTEMPTS:
            time.sleep(_CLAIM_POLL_INTERVAL_SECONDS)
    return None


def describe_validation_record(cert_arn: str, hostname: str, *, credentials: dict, region: str,
                                infra_is_mock: bool, dev_mode: bool) -> dict | None:
    """Single-shot backfill for a claim response that didn't get the record in time —
    called from the list/detail read path, never loops."""
    client = _acm_client(infra_is_mock=infra_is_mock, dev_mode=dev_mode, credentials=credentials, region=region)
    return _extract_validation_record(_describe(client, cert_arn), hostname)


def certificate_status(cert_arn: str, *, credentials: dict, region: str, infra_is_mock: bool, dev_mode: bool) -> str:
    client = _acm_client(infra_is_mock=infra_is_mock, dev_mode=dev_mode, credentials=credentials, region=region)
    return _describe(client, cert_arn).get("Status", "")


def delete_certificate(cert_arn: str, *, credentials: dict, region: str, infra_is_mock: bool, dev_mode: bool) -> bool:
    """Returns True once the certificate is confirmed gone — either just deleted, or
    already not found (idempotent success, e.g. a retry after a previous attempt's
    DeleteCertificate actually succeeded but the caller crashed before recording it).
    Returns False for anything else, most importantly `ResourceInUseException` — ACM
    refuses to delete a certificate still attached to a listener, which for this module
    means the caller's detach step hasn't actually completed despite reporting success:
    a real condition the caller (CustomDomainService._teardown) must retry, never a
    silent no-op it can treat as done (security review R2 — a cert_bootstrap-style
    best-effort delete that always returns success-shaped None here would leave the row
    marked DISABLED while the certificate is still live in the customer's account)."""
    client = _acm_client(infra_is_mock=infra_is_mock, dev_mode=dev_mode, credentials=credentials, region=region)
    try:
        client.delete_certificate(CertificateArn=cert_arn)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            return True
        logger.warning("ACM DeleteCertificate failed for %s (code=%s), will retry", cert_arn, code, exc_info=True)
        return False
    except Exception:
        logger.warning("ACM DeleteCertificate failed for %s, will retry", cert_arn, exc_info=True)
        return False
