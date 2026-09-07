import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from shared.enums.cloud_provider import CloudProvider
from shared.utils.uuid import uuid7_pk

from .reserved_dns_label import ReservedDnsLabel

# Onboarding tokens are short-lived: the customer is expected to run the bootstrap
# script right after creating the infra. A long-lived token widens the replay window
# if the snippet leaks.
ONBOARDING_TOKEN_TTL = timedelta(hours=24)

# 5 attempts against 64 bits of entropy makes exhaustion effectively impossible — the bound
# exists to fail loud on a broken RNG rather than loop forever.
DNS_LABEL_MINT_MAX_ATTEMPTS = 5


class DnsLabelMintExhausted(Exception):
    """Raised when every mint attempt collided with an existing (possibly tombstoned) label."""


class Infrastructure(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid7_pk,
        editable=False,
    )
    name = models.CharField(max_length=255)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='infrastructures',
    )
    cloud_provider = models.CharField(
        max_length=30,
        choices = CloudProvider.choices
    )
    max_cpu = models.FloatField()
    max_memory = models.FloatField()
    is_cloud_authenticated = models.BooleanField(default=False)
    is_mock = models.BooleanField(default=False, editable=False)
    code = models.TextField(null=True, blank=True) # some auth code from cloud provider
    metadata = models.JSONField(null=True, blank=True)
    # Independent random identity for DNS names / cert SANs — never a slice of `id`. `id` is
    # UUIDv7, whose leading 48 bits are a Unix-ms timestamp, so a truncated prefix repeats
    # every ~65s platform-wide and is forceable from the `created_at` on this row's own API
    # response. Minted once via mint_dns_label(); null until then, unique across all rows,
    # never reissued after this row is deleted (see ReservedDnsLabel).
    dns_label = models.CharField(max_length=16, unique=True, null=True, blank=True, editable=False)
    invited_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='invited_infrastructures',
        blank=True,
    )
    # Single-use nonce minted by the dashboard at infra creation; the customer's bootstrap script
    # echoes the plaintext back via the onboarding callback so we can authenticate it without a JWT.
    onboarding_token_hash = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    onboarding_token_used_at = models.DateTimeField(null=True, blank=True)
    onboarding_token_expires_at = models.DateTimeField(null=True, blank=True)
    # Version of LaunchpadDeploymentPolicy the customer last applied, reported by
    # create_aws_role.sh on both the onboarding and policy-refresh callbacks. NULL means
    # onboarded before versioning existed — indistinguishable from stale, so treated as
    # stale. Source of truth for the version itself is
    # api/cloud_providers/aws/iam_policy/policy.json.
    policy_version = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'name')]
        indexes = [
            models.Index(fields=['user', 'is_cloud_authenticated']),
        ]

    @staticmethod
    def hash_token(plaintext: str) -> str:
        return hashlib.sha256(plaintext.encode()).hexdigest()

    @staticmethod
    def parse_reported_policy_version(raw) -> int | None:
        """Coerce the POLICY_VERSION create_aws_role.sh reports on its callbacks.

        Returns None for anything unusable so a malformed value is dropped rather than
        failing an otherwise-valid onboarding — the version is advisory metadata, not an
        auth gate. Clamped to the version Launchpad has actually published: a caller
        claiming a future version would otherwise mark itself current and skip the
        refresh prompt it needs.
        """
        from api.cloud_providers.aws import iam_policy

        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        if value < 1:
            return None
        return min(value, iam_policy.version())

    def policy_refresh_required(self) -> bool:
        from api.cloud_providers.aws import iam_policy

        # NULL means onboarded before versioning existed, which is indistinguishable
        # from stale — treat it as stale so those customers get prompted.
        return self.policy_version is None or self.policy_version < iam_policy.version()

    def issue_onboarding_token(self) -> str:
        # Returns the plaintext exactly once; only the hash is persisted so a DB read cannot replay it.
        plaintext = secrets.token_urlsafe(32)
        self.onboarding_token_hash = self.hash_token(plaintext)
        self.onboarding_token_used_at = None
        self.onboarding_token_expires_at = timezone.now() + ONBOARDING_TOKEN_TTL
        self.save(update_fields=[
            "onboarding_token_hash", "onboarding_token_used_at", "onboarding_token_expires_at",
        ])
        return plaintext

    def mint_dns_label(self) -> str:
        """Mint and persist a unique dns_label. Idempotent — a no-op if one already exists."""
        if self.dns_label:
            return self.dns_label

        for _ in range(DNS_LABEL_MINT_MAX_ATTEMPTS):
            candidate = secrets.token_hex(8)
            try:
                with transaction.atomic():
                    # Reservation is the source of truth; it's written first and never
                    # deleted, so it outlives this row even after a hard delete.
                    ReservedDnsLabel.objects.create(label=candidate, infrastructure_id=self.id)
                    self.dns_label = candidate
                    self.save(update_fields=["dns_label"])
            except IntegrityError:
                # transaction.atomic rolled the DB back, but the attribute assignment above
                # is plain Python and survives it. Clearing it keeps the in-memory row
                # consistent with what is actually persisted, so a caller that catches the
                # exhaustion below can't later save() a label this row never reserved.
                self.dns_label = None
                continue
            return candidate

        raise DnsLabelMintExhausted(
            f"Could not mint a unique dns_label for infrastructure {self.id} after "
            f"{DNS_LABEL_MINT_MAX_ATTEMPTS} attempts."
        )
