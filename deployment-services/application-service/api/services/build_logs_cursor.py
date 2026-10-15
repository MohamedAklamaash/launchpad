"""Signed (not encrypted) pagination cursor for the build-logs endpoint (F7 ticket 8).

Mirrors `runtime_logs_cursor.py`'s shape exactly: the raw CloudWatch `nextForwardToken`s
never reach the client bare — they round-trip signed and bound to the app/user/build they
were issued for, so a client can't replay another user's or another build's cursor, or
widen it to read a different build's log stream by editing the deployment/build-id in the
request. Reuses `RUNTIME_LOGS_CURSOR_SECRET` under a distinct salt rather than requiring a
second cursor secret in every deployment's env — the salt alone gives HMAC domain
separation between the two cursor types, and this cursor protects pagination-tamper, not a
high-value secret.

Two streams (`init`, `kaniko`) means two independent CloudWatch forward tokens, carried
together in one cursor rather than two round-trips.
"""
from django.conf import settings
from django.core import signing

CURSOR_SALT = "build_logs_cursor"
MAX_CURSOR_AGE_SECONDS = 900  # 15 minutes
MAX_CURSOR_BYTES = 4096


class InvalidCursor(ValueError):
    """Any mismatch, tamper, expiry, or oversize cursor collapses to this one error —
    the caller maps it to a single generic 400 so the failure mode itself carries no
    information about which check failed."""


def encode_cursor(*, app_id: str, user_id: str, build_id: str, tokens: dict) -> str:
    payload = {
        "a": str(app_id),
        "u": str(user_id),
        "b": build_id,
        "t": tokens,
    }
    return signing.dumps(payload, key=settings.RUNTIME_LOGS_CURSOR_SECRET, salt=CURSOR_SALT)


def decode_cursor(cursor: str, *, app_id: str, user_id: str, build_id: str) -> dict:
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
        or payload.get("b") != build_id
        or not isinstance(payload.get("t"), dict)
    ):
        # Binding to build_id (not just deployment_id/"current") means a cursor minted
        # mid-build is rejected the moment the application starts its next build and
        # Application.build_id moves on — it can never be replayed against a different
        # build's streams.
        raise InvalidCursor("Invalid cursor")
    return payload
