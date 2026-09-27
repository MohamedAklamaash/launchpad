from django.db import models
from shared.utils.uuid import uuid7_pk


class InfrastructureCertificate(models.Model):
    """Per-infrastructure ACM wildcard certificate state.

    Created and read by the platform DNS writer as input to the DNS reconcile (the
    validation CNAME it must write); populated by the cert bootstrap service (F1b part 2),
    which does not exist yet. All fields below are placeholders until then — nothing in
    part 1 writes cert_arn, validation_name/value, or transitions tls_status.

    `tls_requested_at` is monotonic (set once, never cleared) like Infrastructure's own
    `first_activated_at` pattern, for the same reason: it must be safe to gate destructive
    decisions on it even after a reaper has rewritten Environment.status. Along with
    PlatformDnsRecord, it is one of the two signals `delete_infrastructure` checks before
    allowing a hard delete — never Environment.status, which is not monotonic.
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
