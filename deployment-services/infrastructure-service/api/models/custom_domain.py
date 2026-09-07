from datetime import timedelta

import idna
from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from shared.utils.uuid import uuid7_pk

# Unvalidated claims aren't real ownership evidence — letting one sit forever would let a
# single PENDING row squat a hostname indefinitely against every future claimant.
PENDING_CLAIM_TTL = timedelta(hours=72)


class ReservedSuffixError(ValueError):
    """Hostname is, or falls under, the platform's own reserved apex domain."""


class HostnameAlreadyValidatedError(ValueError):
    """Another CustomDomain row already holds VALIDATED exclusivity on this hostname."""


class IllegalStatusTransitionError(ValueError):
    """Attempted a state-machine transition from a status that doesn't allow it."""


def normalize_hostname(raw: str) -> str:
    """Lowercase + punycode-decode every label, so suffix checks can't be bypassed by mixed
    case, an already-`xn--`-encoded label, or a raw-unicode label (e.g. fullwidth Latin
    lookalikes) that IDNA's UTS-46 compatibility mapping folds down to the reserved suffix.

    Every label is round-tripped through IDNA encode (canonicalizes compatibility/fullwidth
    characters to their ASCII/punycode form) before being decoded back to Unicode for
    comparison — decoding only an already-`xn--`-prefixed label, as a naive implementation
    would, misses raw-unicode homoglyph labels entirely.
    """
    labels = raw.strip().rstrip(".").split(".")
    normalized_labels = []
    for label in labels:
        try:
            ascii_label = idna.encode(label, uts46=True).decode("ascii")
        except idna.IDNAError:
            ascii_label = label
        if ascii_label.lower().startswith("xn--"):
            ascii_label = idna.decode(ascii_label)
        normalized_labels.append(ascii_label.lower())
    return ".".join(normalized_labels)


def reject_reserved_suffix(normalized_hostname: str) -> None:
    suffix = settings.RESERVED_DOMAIN_SUFFIX.lower()
    if normalized_hostname == suffix or normalized_hostname.endswith(f".{suffix}"):
        raise ReservedSuffixError(f"{normalized_hostname!r} falls under the reserved platform domain.")


class CustomDomain(models.Model):
    """Claim-then-validate ownership of a customer-supplied hostname.

    Creating a row only stakes a PENDING claim, not exclusivity — multiple tenants may hold
    PENDING rows on the same hostname simultaneously. Exclusivity is granted only at the
    PENDING -> VALIDATED transition (see the partial unique constraint below), so row
    creation alone can never be used to squat a hostname.

    No AWS/DNS/ACM calls happen anywhere in this model — cert_arn and aws_account_id are
    unpopulated placeholders for a later slice; the actual verifier that calls
    mark_validated()/mark_verification_failed() does not exist yet.
    """

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    infrastructure = models.ForeignKey(
        'Infrastructure',
        on_delete=models.CASCADE,
        related_name='custom_domains',
    )
    # Always stored normalized (see normalize_hostname) — never the raw, unnormalized input.
    hostname = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=[
        ('PENDING', 'Pending'),
        ('VALIDATED', 'Validated'),
        ('DISABLED', 'Disabled'),
    ], default='PENDING')
    # Populated by a future slice once a real cert/account is bound to this hostname.
    cert_arn = models.CharField(max_length=512, null=True, blank=True)
    aws_account_id = models.CharField(max_length=32, null=True, blank=True)
    expires_at = models.DateTimeField()
    last_verified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['hostname', 'status']),
        ]
        constraints = [
            # The actual exclusivity guarantee: at most one row per hostname may ever be
            # VALIDATED. Enforced in the DB, not just in mark_validated(), so a future direct
            # write or a missed select_for_update() can't silently violate it.
            models.UniqueConstraint(
                fields=['hostname'],
                condition=models.Q(status='VALIDATED'),
                name='unique_validated_custom_domain_hostname',
            ),
        ]

    @property
    def is_expired(self) -> bool:
        return self.status == 'PENDING' and self.expires_at < timezone.now()

    @classmethod
    def reserve(cls, hostname: str, infrastructure) -> "CustomDomain":
        """Stake a PENDING claim on `hostname` for `infrastructure`.

        Callers must have already verified `infrastructure` belongs to the requesting user —
        this method trusts its caller on ownership the same way every other model-layer
        write in this service does (see Infrastructure.user); it performs no auth check
        itself.
        """
        normalized = normalize_hostname(hostname)
        reject_reserved_suffix(normalized)
        return cls.objects.create(
            hostname=normalized,
            infrastructure=infrastructure,
            expires_at=timezone.now() + PENDING_CLAIM_TTL,
        )

    def mark_validated(self) -> None:
        if self.status != 'PENDING':
            raise IllegalStatusTransitionError(
                f"Cannot validate a CustomDomain in status {self.status!r}."
            )
        if self.is_expired:
            raise IllegalStatusTransitionError(
                f"PENDING claim on {self.hostname!r} expired at {self.expires_at}."
            )
        try:
            with transaction.atomic():
                locked = CustomDomain.objects.select_for_update().get(pk=self.pk)
                locked.status = 'VALIDATED'
                locked.last_verified_at = timezone.now()
                locked.save(update_fields=['status', 'last_verified_at'])
        except IntegrityError as exc:
            raise HostnameAlreadyValidatedError(
                f"{self.hostname!r} is already validated on another infrastructure."
            ) from exc
        self.status = locked.status
        self.last_verified_at = locked.last_verified_at

    def mark_verification_failed(self) -> None:
        if self.status != 'VALIDATED':
            raise IllegalStatusTransitionError(
                f"Cannot disable a CustomDomain in status {self.status!r}; only a "
                "VALIDATED domain can fail re-validation."
            )
        self.status = 'DISABLED'
        self.save(update_fields=['status'])
