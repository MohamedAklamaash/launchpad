"""Key loading for Application.envs field-level encryption (H1).

Kept dependency-free of Django models/settings so `core/settings.py` can call `load_keys`
directly, the same way it already calls `shared.mode.is_dev_mode` — see the field itself in
`api/fields.py`.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def _derive_dev_fallback_key() -> str:
    """A key-shaped literal must never be committed to the repo (a secret scanner would
    — rightly — flag it, and it invites exactly the copy-paste-into-prod mistake
    `load_keys` refuses below). Deriving it at runtime from a clearly-non-secret,
    human-readable constant gives every `MODE=dev` process the same key (required — it's
    a shared fallback, not a per-process one) without a Fernet key ever appearing in
    source. SHA-256 always yields 32 bytes, exactly what Fernet's URL-safe-base64 key
    format needs.
    """
    digest = hashlib.sha256(b"launchpad-dev-only-envs-key").digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


# Used only when MODE=dev and APP_ENVS_ENCRYPTION_KEYS is unset. Every dev environment
# derives the same key from the same public constant; `load_keys` never returns it (and
# refuses it if explicitly configured) outside dev mode.
DEV_FALLBACK_KEY = _derive_dev_fallback_key()

_KNOWN_WEAK_KEYS = frozenset({DEV_FALLBACK_KEY})


def load_keys(raw: str, is_dev: bool) -> tuple[str, ...]:
    """Parse `APP_ENVS_ENCRYPTION_KEYS` (comma-separated, newest first) and validate every
    entry is a real Fernet key.

    Required outside dev mode: encrypting customer data at rest has no safe default, so a
    missing key must fail startup rather than run unencrypted or fall back to a guessable
    value. Never derived from `DJANGO_SECRET`/`JWT_SECRET` — those already sign
    sessions/CSRF tokens and authenticate service-to-service calls (core/settings.py), and
    a compromise of one secret must not compromise the other.

    Outside dev mode, the derived dev-mode fallback key is refused even if syntactically
    valid — it's derived from a public constant (`_derive_dev_fallback_key`), so accepting
    it in a real deployment would mean "encrypted" data is really encrypted under a key
    anyone can recompute from the source.
    """
    keys = tuple(k.strip() for k in (raw or "").split(",") if k.strip())
    if not keys:
        if is_dev:
            logger.warning(
                "APP_ENVS_ENCRYPTION_KEYS not set — using the fixed MODE=dev key. "
                "Application.envs is NOT confidential in this environment."
            )
            return (DEV_FALLBACK_KEY,)
        raise ValueError(
            "APP_ENVS_ENCRYPTION_KEYS must be set outside MODE=dev — it encrypts "
            "Application.envs at rest and has no safe default."
        )
    for key in keys:
        try:
            Fernet(key)
        except Exception as exc:
            raise ValueError(f"APP_ENVS_ENCRYPTION_KEYS contains an invalid Fernet key: {exc}") from exc
    if not is_dev and _KNOWN_WEAK_KEYS.intersection(keys):
        raise ValueError(
            "APP_ENVS_ENCRYPTION_KEYS outside MODE=dev must not include the derived "
            "MODE=dev fallback key — generate a real key (see env.example)."
        )
    return keys
