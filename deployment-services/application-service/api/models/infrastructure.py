from django.conf import settings
from django.db import models
from shared.enums.cloud_provider import CloudProvider
from shared.enums.orchestrator import ComputeType
from shared.utils.uuid import uuid7_pk


class Infrastructure(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid7_pk,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='infrastructures',
    )
    name = models.CharField(max_length=255)
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

    # F1b part 3a: mirrored from infrastructure-service's own fields via the
    # infrastructure.created and infrastructure.host_readiness_updated events (see
    # api/messaging/consumers/infrastructure.py and api/common/host_url.py). Never derived
    # from this row's own id or created_at — dns_label is independent random entropy minted
    # once upstream and is immutable here, matching infra-service's own invariant.
    dns_label = models.CharField(max_length=16, null=True, blank=True, editable=False)
    tls_status = models.CharField(max_length=20, null=True, blank=True, editable=False)
    dns_synced = models.BooleanField(default=False, editable=False)
    https_ready = models.BooleanField(default=False, editable=False)
    # Bookkeeping only, never exposed via the API: mirrors infrastructure-service's
    # Infrastructure.host_readiness_version, a per-infra monotonic counter incremented
    # under a row lock before every host_readiness_updated publish (RECOMMENDED item 2,
    # security review) — this service's several publishers (a terraform apply, the TLS
    # re-check tick, the DNS writer's own converge loop) give no cross-publisher wall-clock
    # ordering guarantee, so an integer minted by the row itself replaces the wall-clock
    # `occurred_at` this field used to hold. An event missing it is rejected outright, never
    # treated as "always current" — see HostReadinessEventConsumer.
    host_readiness_version = models.PositiveIntegerField(default=0, editable=False)

    # H2 (hardening): mirrored from infrastructure-service's own field via the
    # infrastructure.exited event (see api/messaging/consumers/infrastructure.py). A
    # one-way latch — write-once, exactly like dns_label, never cleared by a later event —
    # since infrastructure-service's own exited_at is itself monotonic (set once by the
    # exit flow, never cleared). Gates deploy/rollback/app-create/webhook-deploy/
    # custom-domain-attach once set.
    exited_at = models.DateTimeField(null=True, blank=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [('user', 'name')]  # Infra name unique per user
        indexes = [
            models.Index(fields=['user', 'is_cloud_authenticated']),
        ]
