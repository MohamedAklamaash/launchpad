from django.db import models
from shared.utils.uuid import uuid7_pk


class Database(models.Model):
    """Read-model for a managed database, populated only by AMQP (environment.updated
    v3's `databases[]`). Never accepts writes from application-service's own API —
    infrastructure-service is the single source of truth for these rows."""

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    environment = models.ForeignKey(
        'Environment',
        on_delete=models.CASCADE,
        related_name='databases',
    )

    name = models.CharField(max_length=32)
    engine = models.CharField(max_length=16)
    host = models.CharField(max_length=255, null=True, blank=True)
    port = models.IntegerField(null=True, blank=True)
    # Secret ARN only — never a credential value.
    secret_arn = models.CharField(max_length=512, null=True, blank=True)
    status = models.CharField(max_length=20, default='PENDING')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'databases'
        indexes = [
            models.Index(fields=['environment', 'status']),
        ]
