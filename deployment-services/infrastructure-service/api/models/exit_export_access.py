from django.db import models
from shared.utils.uuid import uuid7_pk


class ExitExportAccess(models.Model):
    """Durable, append-only, metadata-only access record for the exit-export endpoint
    (F6): who downloaded which infrastructure's exit archive, when, and what happened.
    Denials (401/403/404/429/503) are recorded too, not just successes. Never the archive
    content, never an env value, never a webhook secret — status and size only."""

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    request_id = models.CharField(max_length=36)
    user_id = models.UUIDField()
    infrastructure_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=20, default="export")  # "export" | "complete_exit"
    status_code = models.IntegerField()
    app_count = models.IntegerField(default=0)
    bytes = models.IntegerField(default=0)
    duration_ms = models.IntegerField(default=0)
    client_ip = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "exit_export_access"
        indexes = [
            models.Index(fields=["infrastructure_id", "created_at"], name="exit_export_infra_idx"),
            models.Index(fields=["user_id", "created_at"], name="exit_export_user_idx"),
        ]
