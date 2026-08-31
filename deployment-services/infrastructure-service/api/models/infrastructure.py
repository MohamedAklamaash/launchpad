import hashlib
import secrets
from datetime import timedelta

from django.db import models
from django.conf import settings
from django.utils import timezone
from shared.enums.cloud_provider import CloudProvider
from shared.enums.orchestrator import ComputeType
from shared.utils.uuid import uuid7_pk

# Onboarding tokens are short-lived: the customer is expected to run the bootstrap
# script right after creating the infra. A long-lived token widens the replay window
# if the snippet leaks.
ONBOARDING_TOKEN_TTL = timedelta(hours=24)

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
    compute_type = models.CharField(
        max_length=30,
        choices=ComputeType.choices,
        default=ComputeType.ECS_FARGATE,
    )
    max_cpu = models.FloatField()
    max_memory = models.FloatField()
    is_cloud_authenticated = models.BooleanField(default=False)
    is_mock = models.BooleanField(default=False, editable=False)
    code = models.TextField(null=True, blank=True) # some auth code from cloud provider
    metadata = models.JSONField(null=True, blank=True)
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
