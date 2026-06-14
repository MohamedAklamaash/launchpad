#!/usr/bin/env python3
"""Regression guard: keep LaunchpadDeploymentPolicy in sync across the two
onboarding shell scripts.

Background
----------
`create_aws_role.sh` and `update_aws_role.sh` each embed the same IAM policy
JSON inside a heredoc. They have drifted before (codebuild:* was dropped from
update_aws_role.sh, which silently broke `create_project` for any customer who
ran the refresh script). This check parses the Action entries across ALL
statements of each script and fails CI if the two sets disagree.

Run locally:
    python3 app_scripts/_check_policy_sync.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS = (
    SCRIPT_DIR / "create_aws_role.sh",
    SCRIPT_DIR / "update_aws_role.sh",
)

# The heredoc we care about is the one that writes launchpad-policy.json.
# Both scripts use `cat > "$WORK_DIR/launchpad-policy.json" <<EOF ... EOF`.
_POLICY_HEREDOC_RE = re.compile(
    r'launchpad-policy\.json"?\s*<<EOF\n(.*?)\nEOF',
    re.DOTALL,
)


def _extract_all_actions(script_path: Path) -> list[str]:
    text = script_path.read_text()
    match = _POLICY_HEREDOC_RE.search(text)
    if not match:
        raise SystemExit(
            f"{script_path}: could not locate the launchpad-policy.json heredoc"
        )
    try:
        policy = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{script_path}: heredoc is not valid JSON ({exc})") from exc

    statements = policy.get("Statement") or []
    if not statements:
        raise SystemExit(f"{script_path}: policy has no Statement entries")

    # Collect Action across every statement — drift in iam:* / kms:* (their own
    # statements) was previously invisible because only Statement[0] was checked.
    actions: list[str] = []
    for i, statement in enumerate(statements):
        action = statement.get("Action")
        if isinstance(action, str):
            actions.append(action)
        elif isinstance(action, list):
            actions.extend(action)
        else:
            raise SystemExit(
                f"{script_path}: Statement[{i}].Action must be a string or list, "
                f"got {type(action).__name__}"
            )
    return actions


def main() -> int:
    actions_by_script = {
        script.name: _extract_all_actions(script) for script in SCRIPTS
    }

    # Use set semantics — order in the JSON does not affect IAM evaluation, and
    # forcing order-equality would generate noisy failures for cosmetic edits.
    sets = {name: set(actions) for name, actions in actions_by_script.items()}
    create, update = "create_aws_role.sh", "update_aws_role.sh"
    if sets[create] == sets[update]:
        print(
            f"OK: {create} and {update} share {len(sets[create])} actions across "
            "all statements."
        )
        return 0

    missing_in_update = sorted(sets[create] - sets[update])
    extra_in_update = sorted(sets[update] - sets[create])
    print("FAIL: LaunchpadDeploymentPolicy first-Statement actions diverge.")
    if missing_in_update:
        print(f"  Missing in {update}: {missing_in_update}")
    if extra_in_update:
        print(f"  Extra in {update}:   {extra_in_update}")
    print(
        "Edit both scripts so the Action arrays match. See the NOTE comment above "
        "each heredoc."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
