"""Access record for the exit-export and complete-exit endpoints (F6): a durable DB row
plus a structured log line, written for every outcome — success AND denial. Neither sink
ever receives archive content, an env value, or a webhook secret."""
import logging

from api.models.exit_export_access import ExitExportAccess

audit_logger = logging.getLogger("audit.exit_export")


def record_access(
    *, request_id, user_id, infrastructure_id=None, action="export", status_code,
    app_count=0, total_bytes=0, duration_ms=0, client_ip=None,
):
    fields = {
        "request_id": request_id,
        "user_id": user_id,
        "infrastructure_id": infrastructure_id,
        "action": action,
        "status_code": status_code,
        "app_count": app_count,
        "bytes": total_bytes,
        "duration_ms": duration_ms,
        "client_ip": client_ip,
    }
    ExitExportAccess.objects.create(**fields)
    audit_logger.info("exit_export access", extra=fields)
