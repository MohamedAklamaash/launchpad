"""Signed watermark cursor for the runtime-logs SSE stream, carried as each frame's SSE
`id:` and re-sent by the client as `Last-Event-ID` on reconnect.

Distinct in shape from `runtime_logs_cursor.py`'s cursor: that one wraps an opaque
CloudWatch `nextToken` bound to a *fixed* window (`get_logs` explicitly refuses to combine
a cursor with EKS, which has no such token at all). This one is a resume *watermark*:
the last event timestamp the stream emitted, plus the ids of every event already emitted
at that exact timestamp (CloudWatch event ids tie-break same-millisecond events, empty for
EKS, which has none), so reconnecting can resume a live tail from where it left off on
either compute type without re-deriving a stream's window from this cursor at all.

Reuses RUNTIME_LOGS_CURSOR_SECRET under its own salt: same rationale as
`build_logs_cursor.py`'s reuse. The salt alone gives HMAC domain separation, and this
cursor protects resume-tamper (widening another app/user/container's watermark), not a
high-value secret.
"""
from django.conf import settings
from django.core import signing

CURSOR_SALT = "runtime_logs_stream_cursor"
MAX_CURSOR_AGE_SECONDS = 900  # 15 minutes, matches runtime_logs_cursor.py
MAX_CURSOR_BYTES = 4096
MAX_WATERMARK_IDS = 200


class InvalidCursor(ValueError):
    """Collapses any mismatch, tamper, expiry, or oversize cursor into one error, same
    posture as the other cursor modules: the failure mode carries no information about
    which check failed."""


def encode_watermark(*, app_id, user_id, container: str, ts_ms: int, ids: list) -> str:
    payload = {
        "a": str(app_id),
        "u": str(user_id),
        "c": container,
        "ts": ts_ms,
        "ids": list(ids)[-MAX_WATERMARK_IDS:],
    }
    return signing.dumps(payload, key=settings.RUNTIME_LOGS_CURSOR_SECRET, salt=CURSOR_SALT)


def decode_watermark(cursor: str, *, app_id, user_id, container: str) -> dict:
    if not cursor or len(cursor.encode()) > MAX_CURSOR_BYTES:
        raise InvalidCursor("Invalid cursor")
    try:
        payload = signing.loads(
            cursor, key=settings.RUNTIME_LOGS_CURSOR_SECRET, salt=CURSOR_SALT,
            max_age=MAX_CURSOR_AGE_SECONDS, fallback_keys=[],
        )
    except signing.BadSignature as e:
        raise InvalidCursor("Invalid cursor") from e
    if (
        not isinstance(payload, dict)
        or payload.get("a") != str(app_id)
        or payload.get("u") != str(user_id)
        or payload.get("c") != container
        or not isinstance(payload.get("ts"), int)
        or not isinstance(payload.get("ids"), list)
    ):
        raise InvalidCursor("Invalid cursor")
    return payload
