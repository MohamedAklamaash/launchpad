"""Access record for the runtime-logs endpoint (F2): a durable DB row plus a structured
log line, written for every outcome — success AND denial (403/404/429/503/etc). Neither
sink ever receives log content, the cursor value, or an AWS stream / k8s pod name."""
import logging
from datetime import datetime, timezone

from api.models.runtime_log_access import RuntimeLogAccess

audit_logger = logging.getLogger("audit.runtime_logs")


def _ms_to_dt(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def record_access(
    *, request_id, user_id, app_id=None, infra_id=None, compute_type=None, container=None,
    window_start_ms=None, window_end_ms=None, cursor_present=False, status_code,
    aws_error_code=None, event_count=0, total_bytes=0, duration_ms=0, client_ip=None,
):
    fields = {
        "request_id": request_id,
        "user_id": user_id,
        "app_id": app_id,
        "infra_id": infra_id,
        "compute_type": compute_type,
        "container": container,
        "cursor_present": cursor_present,
        "status_code": status_code,
        "aws_error_code": aws_error_code,
        "event_count": event_count,
        "bytes": total_bytes,
        "duration_ms": duration_ms,
        "client_ip": client_ip,
    }
    RuntimeLogAccess.objects.create(
        window_start=_ms_to_dt(window_start_ms), window_end=_ms_to_dt(window_end_ms), **fields,
    )
    audit_logger.info("runtime_logs access", extra={**fields, "window_start_ms": window_start_ms, "window_end_ms": window_end_ms})
