from django.db import models
from shared.utils.uuid import uuid7_pk


class BuildLogAccess(models.Model):
    """Durable, append-only, metadata-only access record for the build-logs endpoint
    (F7 ticket 8): who tailed which build's logs, when, and what happened — never the
    log content, the signed cursor, or the underlying ECS task id. Denials
    (403/404/409/429/503) are recorded too, not just successes. Mirrors
    `RuntimeLogAccess` (F2)."""

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    request_id = models.CharField(max_length=36)
    user_id = models.UUIDField()
    app_id = models.UUIDField(null=True, blank=True)
    deployment_id = models.UUIDField(null=True, blank=True)
    infra_id = models.UUIDField(null=True, blank=True)
    cursor_present = models.BooleanField(default=False)
    status_code = models.IntegerField()
    aws_error_code = models.CharField(max_length=64, null=True, blank=True)
    event_count = models.IntegerField(default=0)
    bytes = models.IntegerField(default=0)
    duration_ms = models.IntegerField(default=0)
    client_ip = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'build_log_access'
        indexes = [
            models.Index(fields=['app_id', 'created_at'], name='build_log_app_id_idx'),
            models.Index(fields=['user_id', 'created_at'], name='build_log_user_id_idx'),
        ]
