from django.db import models
from shared.utils.uuid import uuid7_pk


class RuntimeLogAccess(models.Model):
    """Durable, append-only, metadata-only access record for the runtime-logs endpoint
    (F2): who tailed which app's logs, when, and what happened — never the log content,
    the signed cursor, or any AWS/k8s stream or pod name. Denials (403/404/429/503) are
    recorded too, not just successes."""

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    request_id = models.CharField(max_length=36)
    user_id = models.UUIDField()
    app_id = models.UUIDField(null=True, blank=True)
    infra_id = models.UUIDField(null=True, blank=True)
    compute_type = models.CharField(max_length=30, null=True, blank=True)
    container = models.CharField(max_length=10, null=True, blank=True)
    window_start = models.DateTimeField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True)
    cursor_present = models.BooleanField(default=False)
    status_code = models.IntegerField()
    aws_error_code = models.CharField(max_length=64, null=True, blank=True)
    event_count = models.IntegerField(default=0)
    bytes = models.IntegerField(default=0)
    duration_ms = models.IntegerField(default=0)
    client_ip = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'runtime_log_access'
        indexes = [
            models.Index(fields=['app_id', 'created_at'], name='runtime_log_app_id_e2f8a1_idx'),
            models.Index(fields=['user_id', 'created_at'], name='runtime_log_user_id_8b3c4d_idx'),
        ]
