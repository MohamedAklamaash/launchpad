import hmac
import secrets
from datetime import timedelta

import idna
from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from shared.utils.uuid import uuid7_pk
from shared.validators.hostname import InvalidHostnameError, validate_hostname_syntax

__all__ = [
    "MAX_CONSECUTIVE_VERIFICATION_FAILURES",
    "PENDING_CLAIM_TTL",
    "CustomDomain",
    "HostnameAlreadyValidatedError",
    "IllegalStatusTransitionError",
    "InvalidHostnameError",
    "ReservedSuffixError",
    "normalize_hostname",
    "reject_reserved_suffix",
    "validate_hostname_syntax",
]

# Unvalidated claims aren't real ownership evidence — letting one sit forever would let a
# single PENDING row squat a hostname indefinitely against every future claimant.
PENDING_CLAIM_TTL = timedelta(hours=72)

# Three consecutive authoritative-TXT failures on a VALIDATED domain disable it (see the
# periodic re-validation job) — one flaky lookup or a transient authoritative-NS outage
# must not disable a working custom domain.
MAX_CONSECUTIVE_VERIFICATION_FAILURES = 3


class ReservedSuffixError(ValueError):
    """Hostname is, or falls under, the platform's own reserved apex domain."""


class HostnameAlreadyValidatedError(ValueError):
    """Another CustomDomain row already holds VALIDATED exclusivity on this hostname."""


class IllegalStatusTransitionError(ValueError):
    """Attempted a state-machine transition from a status that doesn't allow it."""


def normalize_hostname(raw: str) -> str:
    """Lowercase + punycode-encode every label to its canonical ASCII form.

    Every label is round-tripped through IDNA encode with UTS-46 compatibility mapping,
    which folds fullwidth/compatibility characters (e.g. fullwidth Latin lookalikes) down
    to their canonical form before ASCII/punycode conversion — this is what lets the
    suffix check catch a homoglyph label instead of comparing it byte-for-byte against a
    plain ASCII suffix and missing it.

    The result is stored and used everywhere downstream: ALB host-header conditions, ACM
    `DomainName`, and DNS queries all need the ASCII form, not a decoded Unicode display
    string — so unlike an earlier version of this function, an `idna.IDNAError` is not
    swallowed by falling back to the raw label (a label IDNA rejects is not a label ALB,
    ACM, or a resolver should ever see either).
    """
    labels = raw.strip().rstrip(".").split(".")
    normalized_labels = []
    for label in labels:
        if not label:
            raise InvalidHostnameError(f"{raw!r} contains an empty label.")
        try:
            ascii_label = idna.encode(label, uts46=True).decode("ascii")
        except idna.IDNAError as exc:
            raise InvalidHostnameError(f"{raw!r} contains an invalid label {label!r}: {exc}") from exc
        normalized_labels.append(ascii_label.lower())
    return ".".join(normalized_labels)


def reject_reserved_suffix(normalized_hostname: str) -> None:
    suffix = normalize_hostname(settings.RESERVED_DOMAIN_SUFFIX)
    if normalized_hostname == suffix or normalized_hostname.endswith(f".{suffix}"):
        raise ReservedSuffixError(f"{normalized_hostname!r} falls under the reserved platform domain.")

    root = getattr(settings, "PLATFORM_ROOT_DOMAIN", None)
    if root:
        normalized_root = normalize_hostname(root)
        if normalized_hostname == normalized_root or normalized_hostname.endswith(f".{normalized_root}"):
            raise ReservedSuffixError(f"{normalized_hostname!r} falls under the platform's root domain.")


class CustomDomain(models.Model):
    """Claim-then-validate ownership of a customer-supplied hostname.

    Creating a row only stakes a PENDING claim, not exclusivity — multiple tenants may hold
    PENDING rows on the same hostname simultaneously. Exclusivity is granted only at the
    PENDING -> VALIDATED transition (see the partial unique constraint below), so row
    creation alone can never be used to squat a hostname.

    `cert_arn` holds the per-domain ACM certificate ARN, requested at claim time (see
    `api/services/custom_domain_cert.py`); `aws_account_id` is populated once the
    certificate is actually issued in the customer's account. `ownership_token_hash` is
    the sha256 of the random per-claim token the customer publishes as a TXT record at
    `_launchpad-challenge.{hostname}` — only the hash is ever persisted, mirroring
    `Infrastructure.issue_onboarding_token()`.
    """

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    infrastructure = models.ForeignKey(
        'Infrastructure',
        on_delete=models.CASCADE,
        related_name='custom_domains',
    )
    # application-service owns Application; this is a cross-service reference, not a
    # Django FK. Required by the service layer at claim time (see H3 binding: infra +
    # application + hostname) — nullable only so the additive migration never needs to
    # backfill existing rows.
    application_id = models.UUIDField(null=True, blank=True)
    # Always stored normalized (see normalize_hostname) — never the raw, unnormalized input.
    hostname = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=[
        ('PENDING', 'Pending'),
        ('VALIDATED', 'Validated'),
        # An intermediate, retryable checkpoint on the way to DISABLED (security review
        # R2): teardown moves a row here in its own committed transaction BEFORE
        # attempting the ALB detach or the ACM certificate delete — both slow, fallible
        # network calls that must never run with a DB lock held (R1) and must survive a
        # crash or a failed attempt without leaving the row looking VALIDATED/PENDING
        # (silently still "live" with a stale route) or looking DISABLED (falsely
        # implying nothing is attached, and excluded from the periodic sweep's retries).
        # See CustomDomainService._teardown and mark_disabling()/mark_disabled().
        ('DISABLING', 'Disabling'),
        ('DISABLED', 'Disabled'),
    ], default='PENDING')
    cert_arn = models.CharField(max_length=512, null=True, blank=True)
    aws_account_id = models.CharField(max_length=32, null=True, blank=True)
    ownership_token_hash = models.CharField(max_length=64, null=True, blank=True)
    expires_at = models.DateTimeField()
    last_verified_at = models.DateTimeField(null=True, blank=True)
    # Consecutive authoritative-TXT re-validation failures since the last success. Reset
    # to 0 on every successful re-check; MAX_CONSECUTIVE_VERIFICATION_FAILURES trips
    # mark_verification_failed(). Only meaningful for VALIDATED rows.
    verification_failure_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['hostname', 'status']),
            models.Index(fields=['infrastructure', 'application_id']),
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

    @staticmethod
    def hash_ownership_token(plaintext: str) -> str:
        import hashlib
        return hashlib.sha256(plaintext.encode()).hexdigest()

    def issue_ownership_token(self) -> str:
        """Returns the plaintext exactly once; only the hash is persisted so a DB read
        (or a leaked backup) cannot forge the TXT record. Caller must save() the row —
        kept symmetric with reserve()'s single objects.create() call."""
        plaintext = secrets.token_urlsafe(32)
        self.ownership_token_hash = self.hash_ownership_token(plaintext)
        return plaintext

    def check_ownership_token(self, candidate: str) -> bool:
        if not self.ownership_token_hash or not candidate:
            return False
        return hmac.compare_digest(self.ownership_token_hash, self.hash_ownership_token(candidate))

    @classmethod
    def reserve(cls, hostname: str, infrastructure, application_id) -> tuple["CustomDomain", str]:
        """Stake a PENDING claim on `hostname` for `infrastructure`/`application_id`.
        Returns the row and the plaintext ownership token (shown to the caller exactly
        once — see issue_ownership_token).

        Callers must have already verified `infrastructure` belongs to the requesting user
        and that `application_id` is a real application under it — this method trusts its
        caller on ownership the same way every other model-layer write in this service
        does (see Infrastructure.user); it performs no auth check itself.
        """
        normalized = normalize_hostname(hostname)
        validate_hostname_syntax(normalized)
        reject_reserved_suffix(normalized)
        domain = cls(
            hostname=normalized,
            infrastructure=infrastructure,
            application_id=application_id,
            expires_at=timezone.now() + PENDING_CLAIM_TTL,
        )
        plaintext = domain.issue_ownership_token()
        domain.save()
        return domain, plaintext

    def mark_validated(self) -> None:
        """Re-checks status/expiry on the row returned by select_for_update(), not on
        `self` — a caller that read `self` before acquiring the lock could otherwise
        validate a row that expired, or was already transitioned by a concurrent call,
        in the window between its read and this lock."""
        try:
            with transaction.atomic():
                locked = CustomDomain.objects.select_for_update().get(pk=self.pk)
                if locked.status != 'PENDING':
                    raise IllegalStatusTransitionError(
                        f"Cannot validate a CustomDomain in status {locked.status!r}."
                    )
                if locked.is_expired:
                    raise IllegalStatusTransitionError(
                        f"PENDING claim on {locked.hostname!r} expired at {locked.expires_at}."
                    )
                locked.status = 'VALIDATED'
                locked.last_verified_at = timezone.now()
                locked.verification_failure_count = 0
                locked.save(update_fields=['status', 'last_verified_at', 'verification_failure_count'])
        except IntegrityError as exc:
            raise HostnameAlreadyValidatedError(
                f"{self.hostname!r} is already validated on another infrastructure."
            ) from exc
        self.status = locked.status
        self.last_verified_at = locked.last_verified_at
        self.verification_failure_count = locked.verification_failure_count

    def mark_disabling(self) -> None:
        """PENDING or VALIDATED -> DISABLING. Idempotent: already DISABLING or DISABLED
        is a no-op, not an error — teardown may be retried (an owner-triggered delete
        racing the periodic sweep, or a retry after a previous attempt's AWS call
        failed) and must not fail just because a previous attempt already got this far.
        A fast, immediately-committed checkpoint — see the status field's docstring for
        why the slow network calls that follow must run with no lock held at all."""
        with transaction.atomic():
            locked = CustomDomain.objects.select_for_update().get(pk=self.pk)
            if locked.status in ('DISABLING', 'DISABLED'):
                self.status = locked.status
                return
            if locked.status not in ('PENDING', 'VALIDATED'):
                raise IllegalStatusTransitionError(
                    f"Cannot disable a CustomDomain in status {locked.status!r}."
                )
            locked.status = 'DISABLING'
            locked.save(update_fields=['status'])
        self.status = locked.status

    def mark_disabled(self) -> None:
        """DISABLING -> DISABLED, clearing cert_arn — only once the caller has confirmed
        the certificate is actually gone (or already was); keeping the ARN around past
        that point would be a dangling reference. Idempotent: already DISABLED is a
        no-op. Illegal from any status other than DISABLING — the transition must go
        through mark_disabling() first, never straight from PENDING/VALIDATED, or a
        crash between "detach confirmed" and "cert delete confirmed" would have no
        record that detach still needs redoing on retry."""
        with transaction.atomic():
            locked = CustomDomain.objects.select_for_update().get(pk=self.pk)
            if locked.status == 'DISABLED':
                self.status = locked.status
                self.cert_arn = locked.cert_arn
                return
            if locked.status != 'DISABLING':
                raise IllegalStatusTransitionError(
                    f"Cannot mark DISABLED from status {locked.status!r}; must be DISABLING."
                )
            locked.status = 'DISABLED'
            locked.cert_arn = None
            locked.save(update_fields=['status', 'cert_arn'])
        self.status = locked.status
        self.cert_arn = locked.cert_arn

    def record_verification_failure(self) -> bool:
        """Increment the consecutive-failure counter under a row lock; returns True once
        the caller should disable the domain (MAX_CONSECUTIVE_VERIFICATION_FAILURES
        reached). Only meaningful for VALIDATED rows — a no-op otherwise, since a PENDING
        or already-DISABLED row isn't on this counter's state machine."""
        with transaction.atomic():
            locked = CustomDomain.objects.select_for_update().get(pk=self.pk)
            if locked.status != 'VALIDATED':
                self.status = locked.status
                return False
            locked.verification_failure_count += 1
            locked.save(update_fields=['verification_failure_count'])
        self.verification_failure_count = locked.verification_failure_count
        return locked.verification_failure_count >= MAX_CONSECUTIVE_VERIFICATION_FAILURES

    def record_verification_success(self) -> None:
        """Always stamps last_verified_at, even when verification_failure_count was
        already 0 — the common case. An early return here previously skipped the DB
        write on the common path, so revalidate_validated_domains's oldest-first
        ordering (by last_verified_at) never advanced for healthy domains and starved
        them of re-checks in favor of domains that happened to have failed once."""
        now = timezone.now()
        CustomDomain.objects.filter(pk=self.pk).update(
            verification_failure_count=0, last_verified_at=now,
        )
        self.verification_failure_count = 0
        self.last_verified_at = now
