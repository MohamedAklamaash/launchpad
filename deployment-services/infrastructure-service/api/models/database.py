import hashlib

from django.db import models
from shared.utils.uuid import uuid7_pk


class Database(models.Model):
    """Desired-state row for one managed database inside an environment.

    Reconciled by the same Terraform worker/queue/lock/reaper as the environment itself —
    every create/delete is one more module block in that environment's generated config.
    """

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    environment = models.ForeignKey(
        'Environment',
        on_delete=models.CASCADE,
        related_name='databases',
    )

    name = models.CharField(max_length=32)
    engine = models.CharField(max_length=16, choices=[
        ('postgres', 'PostgreSQL'),
        ('mysql', 'MySQL'),
        ('redis', 'Redis'),
        ('docdb', 'DocumentDB'),
    ])
    engine_version = models.CharField(max_length=16)
    instance_class = models.CharField(max_length=32)
    # Not applicable to Redis (sized by node type only) — null there.
    allocated_storage = models.IntegerField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=[
        ('PENDING', 'Pending'),
        ('PROVISIONING', 'Provisioning'),
        ('ACTIVE', 'Active'),
        ('ERROR', 'Error'),
        ('DELETING', 'Deleting'),
        ('DELETED', 'Deleted'),
    ], default='PENDING')
    host = models.CharField(max_length=255, null=True, blank=True)
    port = models.IntegerField(null=True, blank=True)
    # Secret ARN only — never a credential value. See goal 2 of the plan: no
    # credential-shaped field on this row, in any serializer, or on the AMQP payload.
    secret_arn = models.CharField(max_length=512, null=True, blank=True)

    # Fixed at create time from this row's own UUID — never recomputed at delete time,
    # so a delete-then-recreate at the same name can't collide on a static identifier.
    final_snapshot_id = models.CharField(max_length=255, null=True, blank=True)

    error_message = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'databases'
        indexes = [
            models.Index(fields=['environment', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['environment', 'name'],
                condition=~models.Q(status='DELETED'),
                name='unique_live_database_name_per_environment',
            ),
        ]

    def module_name(self) -> str:
        """Deterministic per-row Terraform module block name.

        Hashed rather than a raw slice of the UUID: this id is uuid7, whose leading
        bits are a millisecond timestamp, not randomness — two rows created close
        together (e.g. the same request) would otherwise collide on the same module
        name and silently clobber each other's HCL block.
        """
        return f"db_{hashlib.md5(str(self.id).encode()).hexdigest()[:8]}"

    def snapshot_identifier(self) -> str:
        return f"lp-final-{self.id}"

    def save(self, *args, **kwargs):
        # Fixed at creation from this row's own (immutable) UUID — a delete-then-recreate
        # at the same name must never collide on a snapshot identifier computed later.
        if not self.final_snapshot_id:
            self.final_snapshot_id = self.snapshot_identifier()
        super().save(*args, **kwargs)
