import json

from django.utils.crypto import salted_hmac

# Domain-separates this hash from Django's other uses of SECRET_KEY (session signing,
# CSRF, password reset tokens, ...) — a collision or a weakness discovered in one context
# must not carry over into another.
_KEY_SALT = "launchpad.deployment.env_values"


def snapshot_env(app_id, envs: dict) -> tuple[list[str], str]:
    """Return the env's shape, never its values — the sorted key names and a keyed,
    per-app hash of the values.

    `salted_hmac` derives its key from SECRET_KEY and `_KEY_SALT` rather than hashing
    SECRET_KEY directly, so the digest can't be dictionary-attacked into a value guess and
    doesn't reuse the same keyed hash Django uses elsewhere. Folding the application id into
    the hashed value means two different apps that happen to have byte-identical envs never
    compare equal. Canonical JSON (sorted keys, tight separators) makes the digest depend
    only on the key/value pairs, not on dict insertion order, so two deploys of the same env
    compare equal regardless of how the dict was built.
    """
    envs = envs or {}
    keys = sorted(envs.keys())
    canonical = json.dumps([[k, envs[k]] for k in keys], sort_keys=True, separators=(",", ":"))
    digest = salted_hmac(_KEY_SALT, f"{app_id}:{canonical}", algorithm="sha256").hexdigest()
    return keys, digest
