"""Canonical source of truth for the IAM policy Launchpad asks customers to attach to
`LaunchpadDeploymentRole`.

`policy.json` is the only place the action list is written down. Everything customers
see — the heredoc in `app_scripts/create_aws_role.sh`, both JSON blocks in
`docs/IAM_POLICIES.md` — is rendered from it by `generate.py`, and CI fails if the
rendered output drifts from what is committed.

Stdlib only, and deliberately free of Django imports: `generate.py` runs this in CI
without a settings module, and the Django side imports it for `policy_version`.
"""

import functools
import hashlib
import json
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parent / "policy.json"

# The IAM policy-language version, not ours. It is grammar, not a knob, so it lives here
# rather than in policy.json where a reader might mistake it for the Launchpad version.
IAM_POLICY_LANGUAGE_VERSION = "2012-10-17"


@functools.lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(POLICY_PATH.read_text())


def version() -> int:
    return int(load()["version"])


def notes() -> list[str]:
    return list(load()["notes"])


def statements() -> list[dict]:
    return json.loads(json.dumps(load()["statements"]))


def version_hashes() -> dict[str, str]:
    return dict(load()["version_hashes"])


def document() -> dict:
    """The policy document exactly as it is written into the customer's account."""
    return {"Version": IAM_POLICY_LANGUAGE_VERSION, "Statement": statements()}


def document_json() -> str:
    return json.dumps(document(), indent=2)


def statements_hash() -> str:
    """Content hash of the granted permissions alone.

    Bound to `version` through `version_hashes` so editing the action list without
    bumping the version is a CI failure. Without that binding `policy_version` on an
    Infrastructure row would silently claim a permission set the customer never applied.
    Notes and formatting are excluded — only what AWS actually evaluates counts.
    """
    canonical = json.dumps(statements(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def grants(action: str) -> bool:
    """Whether the policy allows `action` (e.g. "rds:CreateDBInstance").

    Handles the `service:*` and `*` wildcards the policy actually uses. Deny statements
    are not modelled because the policy has none; `assert_no_deny_statements` in the
    generator keeps that assumption honest.
    """
    for statement in statements():
        if statement.get("Effect") != "Allow":
            continue
        granted = statement.get("Action")
        candidates = [granted] if isinstance(granted, str) else granted
        for candidate in candidates:
            if candidate == "*" or candidate == action:
                return True
            if candidate.endswith(":*") and action.startswith(candidate[:-1]):
                return True
    return False
