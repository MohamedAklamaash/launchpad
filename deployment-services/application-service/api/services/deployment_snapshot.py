import hashlib
import hmac
import json

from django.conf import settings


def snapshot_env(envs: dict) -> tuple[list[str], str]:
    """Return the env's shape, never its values — the sorted key names and a keyed hash
    of the values.

    HMAC rather than a plain digest so the hash can't be dictionary-attacked into a value
    guess. Keyed on SECRET_KEY, which `core.settings.validate_config` already requires to be
    50+ characters — no new secret to provision or rotate separately. Canonical JSON (sorted
    keys, tight separators) makes the digest depend only on the key/value pairs, not on
    dict insertion order, so two deploys with the same env compare equal.
    """
    envs = envs or {}
    keys = sorted(envs.keys())
    canonical = json.dumps([[k, envs[k]] for k in keys], sort_keys=True, separators=(",", ":"))
    digest = hmac.new(settings.SECRET_KEY.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return keys, digest
