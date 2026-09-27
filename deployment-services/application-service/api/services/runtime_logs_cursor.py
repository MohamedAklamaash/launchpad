"""Signed (not encrypted) pagination cursor for the runtime-logs endpoint (F2).

The raw CloudWatch `nextToken` never reaches the client: it round-trips signed and bound
to the app/user/container/window it was issued for, so a client can't replay another
user's or another app's cursor, or tamper with the window to widen it beyond what the
original request was authorized for. `django.core.signing` only signs — the payload is
base64/JSON, readable by anyone holding the cursor, which is fine: it carries no more than
what the request that produced it already disclosed to that same client (app id, user id,
container, window, and an opaque AWS pagination token).
"""
from django.conf import settings
from django.core import signing

CURSOR_SALT = "runtime_logs_cursor"
MAX_CURSOR_AGE_SECONDS = 900  # 15 minutes
MAX_CURSOR_BYTES = 4096


class InvalidCursor(ValueError):
    """Any mismatch, tamper, expiry, or oversize cursor collapses to this one error —
    the caller maps it to a single generic 400 so the failure mode itself carries no
    information about which check failed."""


def encode_cursor(*, app_id: str, user_id: str, container: str, start_ms: int, end_ms: int, aws_token: str) -> str:
    payload = {
        "a": str(app_id),
        "u": str(user_id),
        "c": container,
        "s": start_ms,
        "e": end_ms,
        "t": aws_token,
    }
    return signing.dumps(payload, key=settings.RUNTIME_LOGS_CURSOR_SECRET, salt=CURSOR_SALT)


def decode_cursor(cursor: str, *, app_id: str, user_id: str, container: str) -> dict:
    if not cursor or len(cursor.encode()) > MAX_CURSOR_BYTES:
        raise InvalidCursor("Invalid cursor")
    try:
        # fallback_keys=[]: without this, Signer defaults to settings.SECRET_KEY_FALLBACKS
        # (Django 5.2) — the main platform secret's rotation list, a different secret
        # domain entirely. A cursor must verify against RUNTIME_LOGS_CURSOR_SECRET alone.
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
    ):
        raise InvalidCursor("Invalid cursor")
    return payload
