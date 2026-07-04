import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from shared.utils.uuid import uuid7_pk


class ScriptApiKey(models.Model):
    """Long-lived per-user API key that authenticates customer-run refreshes
    (create_aws_role.sh with a script API key) back to the platform, so we can
    record WHO ran an IAM refresh. Same storage contract as the onboarding token: only the SHA-256
    hash is persisted; the plaintext (prefix ``lp_``) is shown exactly once."""

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='script_api_keys',
    )
    key_hash = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def hash_key(plaintext: str) -> str:
        return hashlib.sha256(plaintext.encode()).hexdigest()

    @classmethod
    def issue(cls, user) -> str:
        # One active key per user: issuing rotates, so a leaked older snippet
        # stops authenticating the moment the user generates a fresh key.
        plaintext = f"lp_{secrets.token_urlsafe(32)}"
        cls.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
        cls.objects.create(user=user, key_hash=cls.hash_key(plaintext))
        return plaintext

    @classmethod
    def authenticate(cls, plaintext):
        """Return the active key matching ``plaintext``, or None.

        Lookup is by SHA-256 hash (unique column), so no timing oracle on the
        plaintext: an attacker learns nothing beyond key-valid/key-invalid.
        """
        if not plaintext:
            return None
        try:
            return cls.objects.select_related('user').get(
                key_hash=cls.hash_key(plaintext), revoked_at__isnull=True,
            )
        except cls.DoesNotExist:
            return None
