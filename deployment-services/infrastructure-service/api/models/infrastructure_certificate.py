from django.db import models
from shared.utils.uuid import uuid7_pk


class InfrastructureCertificate(models.Model):
    """Per-infrastructure ACM wildcard certificate state.

    Created and read by the platform DNS writer as input to the DNS reconcile (the
    validation CNAME it must write); populated by the cert bootstrap service (F1b part 2),
    which does not exist yet. All fields below are placeholders until then — nothing in
    part 1 writes cert_arn, validation_name/value, or transitions tls_status.

    `tls_requested_at` is monotonic (set once, never cleared) like Infrastructure's own
    `first_activated_at` pattern — safe to use as a signal that TLS teardown should run for
    this infra, even after a reaper has rewritten Environment.status. It is deliberately
    NOT one of the conditions `delete_infrastructure` checks before allowing a hard delete:
    since it is never cleared, gating a delete on it would make any infrastructure that
    ever requested a certificate permanently undeletable. Only a live PlatformDnsRecord
    ledger row blocks a hard delete (see teardown.py:has_live_dns_state) — never
    Environment.status, and never this field.
    """

    TLS_PENDING = "PENDING"
    TLS_ISSUED = "ISSUED"
    TLS_FAILED = "FAILED"
    TLS_STATUS_CHOICES = [
        (TLS_PENDING, "Pending"),
        (TLS_ISSUED, "Issued"),
        (TLS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    infrastructure = models.OneToOneField(
        "Infrastructure",
        on_delete=models.CASCADE,
        related_name="certificate",
    )
    cert_arn = models.CharField(max_length=255, null=True, blank=True)
    validation_name = models.CharField(max_length=255, null=True, blank=True)
    validation_value = models.CharField(max_length=512, null=True, blank=True)
    tls_status = models.CharField(max_length=20, choices=TLS_STATUS_CHOICES, default=TLS_PENDING)
    tls_requested_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
