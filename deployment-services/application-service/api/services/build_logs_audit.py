"""Access record for the build-logs endpoint (F7 ticket 8): a durable DB row plus a
structured log line, written for every outcome — success AND denial (403/404/409/429/etc).
Neither sink ever receives log content, the cursor value, or the underlying ECS task id.
Mirrors `runtime_logs_audit.py` (F2)."""
import logging

from api.models.build_log_access import BuildLogAccess

audit_logger = logging.getLogger("audit.build_logs")


def record_access(
    *, request_id, user_id, app_id=None, deployment_id=None, infra_id=None,
    cursor_present=False, status_code, aws_error_code=None, event_count=0, total_bytes=0,
    duration_ms=0, client_ip=None,
):
    fields = {
        "request_id": request_id,
        "user_id": user_id,
        "app_id": app_id,
        "deployment_id": deployment_id,
        "infra_id": infra_id,
        "cursor_present": cursor_present,
        "status_code": status_code,
        "aws_error_code": aws_error_code,
        "event_count": event_count,
        "bytes": total_bytes,
        "duration_ms": duration_ms,
        "client_ip": client_ip,
    }
    try:
        BuildLogAccess.objects.create(**fields)
    except Exception:
        # A durable-row failure must never turn an otherwise-successful (or correctly
        # denied) response into a 500 — the structured log line right below is written
        # either way, so the access is never silently unaudited, just missing its DB row.
        audit_logger.exception("build_logs access row could not be written", extra=fields)
    audit_logger.info("build_logs access", extra=fields)
