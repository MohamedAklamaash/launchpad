import hmac
import ipaddress
import re
import secrets
from datetime import timedelta

import idna
from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from shared.utils.uuid import uuid7_pk

# Unvalidated claims aren't real ownership evidence — letting one sit forever would let a
# single PENDING row squat a hostname indefinitely against every future claimant.
PENDING_CLAIM_TTL = timedelta(hours=72)

# Three consecutive authoritative-TXT failures on a VALIDATED domain disable it (see the
# periodic re-validation job) — one flaky lookup or a transient authoritative-NS outage
# must not disable a working custom domain.
MAX_CONSECUTIVE_VERIFICATION_FAILURES = 3

_LDH_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_MAX_HOSTNAME_LENGTH = 253
_MAX_LABEL_LENGTH = 63


class ReservedSuffixError(ValueError):
    """Hostname is, or falls under, the platform's own reserved apex domain."""


class InvalidHostnameError(ValueError):
    """Hostname fails basic DNS syntax before it may reach ALB conditions, ACM, or a
    resolver."""


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


def validate_hostname_syntax(hostname: str) -> None:
    """Enforce plain DNS hostname syntax on an already-`normalize_hostname`d (ASCII/
    punycode, lowercased) value, before it can reach an ALB host-header condition, ACM's
    `DomainName`, or a DNS query. Apex/root domains are allowed through here (>=2 labels
    only) — rejecting apex is a documented product decision (see F1b's "Out of scope"),
    not a syntax rule, and is enforced separately if ever needed.
    """
    if len(hostname) > _MAX_HOSTNAME_LENGTH:
        raise InvalidHostnameError(f"{hostname!r} exceeds {_MAX_HOSTNAME_LENGTH} characters.")
    if "*" in hostname:
        raise InvalidHostnameError(f"{hostname!r} contains a wildcard, which is not a claimable hostname.")

    labels = hostname.split(".")
    if len(labels) < 2:
        raise InvalidHostnameError(f"{hostname!r} must have at least two labels.")
    for label in labels:
        if len(label) > _MAX_LABEL_LENGTH:
            raise InvalidHostnameError(f"label {label!r} in {hostname!r} exceeds {_MAX_LABEL_LENGTH} characters.")
        if not _LDH_LABEL_RE.match(label):
            raise InvalidHostnameError(f"label {label!r} in {hostname!r} is not a valid LDH label.")

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise InvalidHostnameError(f"{hostname!r} is an IP address literal, not a hostname.")


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

    def mark_verification_failed(self) -> None:
        with transaction.atomic():
            locked = CustomDomain.objects.select_for_update().get(pk=self.pk)
            if locked.status != 'VALIDATED':
                raise IllegalStatusTransitionError(
                    f"Cannot disable a CustomDomain in status {locked.status!r}; only a "
                    "VALIDATED domain can fail re-validation."
                )
            locked.status = 'DISABLED'
            locked.save(update_fields=['status'])
        self.status = locked.status

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
        if self.verification_failure_count == 0:
            return
        CustomDomain.objects.filter(pk=self.pk).update(
            verification_failure_count=0, last_verified_at=timezone.now(),
        )
        self.verification_failure_count = 0
