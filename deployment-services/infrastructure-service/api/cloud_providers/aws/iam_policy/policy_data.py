"""Canonical source of truth for the IAM policy Launchpad asks customers to attach to
`LaunchpadDeploymentRole`.

`policy.json` is the only place the action list is written down. Everything customers
see — the heredoc in `app_scripts/create_aws_role.sh`, the JSON blocks in
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

# The compute_type create_aws_role.sh assumes when LAUNCHPAD_COMPUTE_TYPE is unset. The
# script's own default must match this literal; test_policy_generator.py asserts it.
DEFAULT_COMPUTE_TYPE = "ecs_fargate"

# The only placeholder a rendered policy is allowed to carry. It stands for the
# customer's AWS account id and is substituted by the script at run time, never by
# Python — the account id isn't known until the script runs in the customer's shell.
ACCOUNT_ID_PLACEHOLDER = "__LAUNCHPAD_ACCOUNT_ID__"


@functools.lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(POLICY_PATH.read_text())


def version() -> int:
    return int(load()["version"])


def notes() -> list[str]:
    return list(load()["notes"])


def statements() -> list[dict]:
    return json.loads(json.dumps(load()["statements"]))


def compute_type_statements() -> dict[str, list[dict]]:
    """Extra statements appended to the base statements for a given compute_type, e.g.
    the EKS-only grants applied when LAUNCHPAD_COMPUTE_TYPE=eks."""
    return json.loads(json.dumps(load().get("compute_type_statements", {})))


def version_hashes() -> dict[str, str]:
    return dict(load()["version_hashes"])


def document(compute_type: str | None = None) -> dict:
    """The policy document exactly as it is written into the customer's account.

    With no argument this is the base document, byte-identical regardless of
    compute_type, since that's what ecs_fargate (the default) applies. Passing a
    compute_type appends that type's extra statements, e.g. document("eks").
    """
    statement_list = statements()
    if compute_type is not None:
        statement_list = statement_list + compute_type_statements().get(compute_type, [])
    return {"Version": IAM_POLICY_LANGUAGE_VERSION, "Statement": statement_list}


def document_json(compute_type: str | None = None) -> str:
    return json.dumps(document(compute_type), indent=2)


def statements_hash() -> str:
    """Content hash of the granted permissions alone: base statements plus every
    compute_type's extras.

    Bound to `version` through `version_hashes` so editing the action list, base or
    compute-type, without bumping the version is a CI failure. Without that binding
    `policy_version` on an Infrastructure row would silently claim a permission set the
    customer never applied. Notes and formatting are excluded — only what AWS actually
    evaluates counts.
    """
    canonical = json.dumps(
        {"statements": statements(), "compute_type_statements": compute_type_statements()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def grants(action: str) -> bool:
    """Whether the base policy allows `action` (e.g. "rds:CreateDBInstance").

    Only walks the base statements applied to every compute_type; `iam_precheck`
    simulates create actions that are all ECS/database-side. Handles the `service:*`
    and `*` wildcards the base policy actually uses. Deny statements in the base
    statements are not modelled because the base policy has none (compute-type extras,
    e.g. EKS, may carry a Deny; `_assert_invariants` in the generator only enforces
    "no Deny" on the base statements, not on those).
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
