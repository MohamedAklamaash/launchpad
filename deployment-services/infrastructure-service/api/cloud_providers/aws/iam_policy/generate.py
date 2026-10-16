"""Render every customer-facing copy of the deployment policy from `policy.json`.

    python .../iam_policy/generate.py --check    # CI gate: fail if committed output drifted
    python .../iam_policy/generate.py --write    # regenerate after editing policy.json

The policy used to exist only as a bash heredoc, hand-duplicated twice in
`docs/IAM_POLICIES.md`. Three copies of a security boundary is three chances to drift,
and a drifted doc is what a customer's security reviewer reads. Now the heredoc and the
doc blocks are generated regions, and `--check` runs in CI.

`create_aws_role.sh` stays self-contained, offline-auditable bash — the policy is baked
into the file at commit time, never fetched from an API at run time.

Stdlib only, and no Django: CI runs this without a settings module.
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from . import policy_data
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import policy_data

# .../deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy
REPO_ROOT = Path(__file__).resolve().parents[6]

SCRIPT_PATH = REPO_ROOT / "app_scripts" / "create_aws_role.sh"
DOCS_PATH = REPO_ROOT / "docs" / "IAM_POLICIES.md"

SOURCE_HINT = "deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/policy.json"

# Whole-file generated templates, not delimited regions: nobody hand-edits a
# .template.json, so the entire file is machine output. One per compute_type, mirroring
# how policy_data.document(compute_type) already produces two concrete documents.
CFN_DIR = Path(__file__).resolve().parent / "cloudformation"
CFN_TEMPLATE_PATHS = {
    "ecs_fargate": CFN_DIR / "launchpad-deployment-role.ecs-fargate.template.json",
    "eks": CFN_DIR / "launchpad-deployment-role.eks.template.json",
}

# Any dunder-style token in a rendered policy that isn't the one recognized account-id
# placeholder. Catches a future placeholder introduced without generator/script support.
_PLACEHOLDER_RE = re.compile(r"__[A-Z_]+__")

# What the account-id placeholder becomes inside an Fn::Sub string. CFN's own
# pseudo-parameter replaces the script's `sed` substitution; no customer action needed.
_CFN_ACCOUNT_ID_SUB = "${AWS::AccountId}"


class DriftError(Exception):
    """A generated region on disk does not match what policy.json renders."""


def _rel(path: Path) -> str:
    """Repo-relative for humans, absolute when the path is outside the repo (tests)."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _script_region() -> str:
    lines = ["# Permissions granted to Launchpad in YOUR account:"]
    for note in policy_data.notes():
        # A note indented in the JSON is a continuation of the previous bullet.
        lines.append(f"# {note}" if note.startswith("  ") else f"# - {note}")
    lines += [
        "# Review before running. To narrow scope, edit launchpad-policy.json before this script runs.",
        f"POLICY_VERSION={policy_data.version()}",
        'case "$COMPUTE_TYPE" in',
        f"  {policy_data.DEFAULT_COMPUTE_TYPE})",
        '    cat > "$WORK_DIR/launchpad-policy.json" <<\'EOF\'',
        policy_data.document_json(),
        "EOF",
        "    ;;",
        "  eks)",
        '    cat > "$WORK_DIR/launchpad-policy.eks.json" <<\'EOF\'',
        policy_data.document_json("eks"),
        "EOF",
        f"    # {policy_data.ACCOUNT_ID_PLACEHOLDER} is a literal placeholder substituted here, not a",
        "    # shell variable; the heredoc above stays quoted so an IAM action can never be",
        "    # read as a shell expansion.",
        (
            f'    sed "s/{policy_data.ACCOUNT_ID_PLACEHOLDER}/${{ACCOUNT_ID}}/g" '
            f'"$WORK_DIR/launchpad-policy.eks.json" > "$WORK_DIR/launchpad-policy.json"'
        ),
        "    ;;",
        "  *)",
        (
            f'    echo "ERROR: unknown LAUNCHPAD_COMPUTE_TYPE \'${{COMPUTE_TYPE}}\' '
            f'(expected \\"{policy_data.DEFAULT_COMPUTE_TYPE}\\" or \\"eks\\")." >&2'
        ),
        "    exit 1",
        "    ;;",
        "esac",
    ]
    return "\n".join(lines)


def _docs_json_region() -> str:
    return "```json\n" + policy_data.document_json() + "\n```"


def _docs_eks_region() -> str:
    return "```json\n" + json.dumps(policy_data.compute_type_statements()["eks"], indent=2) + "\n```"


def _docs_cli_region() -> str:
    return "cat > deployment-policy.json <<'EOF'\n" + policy_data.document_json() + "\nEOF"


def _docs_version_region() -> str:
    return f"**Policy version**: {policy_data.version()}"


def _cfn_resource_field(value):
    """Wrap any string containing the account-id placeholder in an Fn::Sub, recursing
    into lists (Resource/NotResource may be a bare string or a list of strings).
    Strings without the placeholder pass through untouched — this is the only
    rendering difference between the script's heredoc and the CFN template; Action,
    Effect, and Condition are never touched."""
    if isinstance(value, list):
        return [_cfn_resource_field(item) for item in value]
    if isinstance(value, str) and policy_data.ACCOUNT_ID_PLACEHOLDER in value:
        return {"Fn::Sub": value.replace(policy_data.ACCOUNT_ID_PLACEHOLDER, _CFN_ACCOUNT_ID_SUB)}
    return value


def _cfn_statement(stmt: dict) -> dict:
    """One policy statement, rendered for the CFN ManagedPolicy's PolicyDocument."""
    rendered = dict(stmt)
    for key in ("Resource", "NotResource"):
        if key in rendered:
            rendered[key] = _cfn_resource_field(rendered[key])
    return rendered


def _cfn_compute_types_tag(compute_type: str) -> str:
    """Static, per-template literal — templates are files with no account-level state
    to compute a union from. The eks template over-reports relative to a lone
    ecs_fargate sibling's actual grants, which is harmless (see generate.py's CFN
    template docstring / plan/F8-cloudformation-onboarding.md)."""
    return "ecs_fargate+eks" if compute_type == "eks" else policy_data.DEFAULT_COMPUTE_TYPE


def _cfn_template(compute_type: str) -> dict:
    version = policy_data.version()
    statements = [_cfn_statement(s) for s in policy_data.document(compute_type)["Statement"]]

    role_properties = {
        "RoleName": "LaunchpadDeploymentRole",
        "AssumeRolePolicyDocument": {
            "Version": policy_data.IAM_POLICY_LANGUAGE_VERSION,
            "Statement": [
                {
                    "Effect": "Allow",
                    # Fn::Join, not Fn::Sub: Fn::Sub is reserved exclusively for
                    # _cfn_statement's __LAUNCHPAD_ACCOUNT_ID__ wrapping, so "the
                    # ecs_fargate template contains zero Fn::Sub objects" stays literally
                    # true and the round-trip check only ever has to reason about
                    # PolicyDocument statements.
                    "Principal": {
                        "AWS": {
                            "Fn::Join": [
                                "",
                                [
                                    "arn:aws:iam::",
                                    {"Ref": "LaunchpadPlatformAccountId"},
                                    ":user/",
                                    {"Ref": "LaunchpadPlatformUser"},
                                ],
                            ]
                        }
                    },
                    "Action": "sts:AssumeRole",
                    "Condition": {"StringEquals": {"sts:ExternalId": {"Ref": "ExternalIds"}}},
                }
            ],
        },
        "Tags": [
            {"Key": "launchpad:managed-by", "Value": "cloudformation"},
            {"Key": "launchpad:policy-version", "Value": str(version)},
            {"Key": "launchpad:compute-types", "Value": _cfn_compute_types_tag(compute_type)},
            {"Key": "launchpad:cfn-stack-name", "Value": {"Ref": "AWS::StackName"}},
        ],
    }
    # Mirrors the script's conditional --max-session-duration: omitted (AWS default
    # 3600) for ecs_fargate, baked in only for eks.
    if compute_type == "eks":
        role_properties["MaxSessionDuration"] = 7200

    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": (
            f"Launchpad deployment role + policy ({compute_type}, policy v{version}). "
            "Generated from policy.json, do not hand-edit."
        ),
        "Parameters": {
            "ExternalIds": {
                "Type": "CommaDelimitedList",
                "Description": (
                    "ExternalId(s) this role trusts. One per infra sharing this AWS "
                    "account; always a list, even for a single infra."
                ),
            },
            "LaunchpadPlatformAccountId": {
                "Type": "String",
                "AllowedPattern": "^[0-9]{12}$",
                "Description": "Launchpad platform AWS account id named as trust policy principal.",
            },
            "LaunchpadPlatformUser": {
                "Type": "String",
                "Description": "Launchpad platform IAM user name named as trust policy principal.",
            },
        },
        "Resources": {
            "LaunchpadDeploymentRole": {
                "Type": "AWS::IAM::Role",
                "Properties": role_properties,
            },
            "LaunchpadDeploymentPolicy": {
                "Type": "AWS::IAM::ManagedPolicy",
                "Properties": {
                    "ManagedPolicyName": "LaunchpadDeploymentPolicy",
                    "Roles": [{"Ref": "LaunchpadDeploymentRole"}],
                    "PolicyDocument": {
                        "Version": policy_data.IAM_POLICY_LANGUAGE_VERSION,
                        "Statement": statements,
                    },
                },
            },
        },
        "Outputs": {
            "PolicyVersion": {
                "Value": str(version),
                "Description": "Convenience echo of launchpad:policy-version for a human reading the console.",
            }
        },
    }


def _cfn_template_json(compute_type: str) -> str:
    return json.dumps(_cfn_template(compute_type), indent=2) + "\n"


# (path, marker name, renderer). Marker names are the literal text between the comment
# delimiters, so a grep for "BEGIN GENERATED" finds every region this file owns.
REGIONS = [
    (SCRIPT_PATH, "#", "deployment policy", _script_region),
    (DOCS_PATH, "<!--", "deployment policy", _docs_json_region),
    (DOCS_PATH, "<!--", "deployment policy (eks)", _docs_eks_region),
    (DOCS_PATH, "#", "deployment policy (cli)", _docs_cli_region),
    (DOCS_PATH, "<!--", "policy version", _docs_version_region),
]


def _markers(comment: str, name: str) -> tuple[str, str]:
    if comment == "<!--":
        return (f"<!-- BEGIN GENERATED: {name} — source: {SOURCE_HINT} -->",
                "<!-- END GENERATED -->")
    return (f"# BEGIN GENERATED: {name} — source: {SOURCE_HINT}",
            "# END GENERATED")


def _replace_region(text: str, comment: str, name: str, body: str, path: Path) -> str:
    begin, end = _markers(comment, name)
    pattern = re.compile(
        re.escape(begin) + r"\n.*?\n" + re.escape(end),
        re.DOTALL,
    )
    replacement = f"{begin}\n{body}\n{end}"
    new_text, count = pattern.subn(lambda _: replacement, text, count=1)
    if count != 1:
        raise DriftError(
            f"{_rel(path)}: expected exactly one region delimited by {begin!r} … {end!r}, found {count}. "
            "The markers were edited or removed — restore them before regenerating."
        )
    return new_text


def _assert_invariants() -> None:
    compute_types = [None, *sorted(policy_data.compute_type_statements())]
    for compute_type in compute_types:
        rendered = policy_data.document_json(compute_type)

        # The script writes this JSON through a quoted heredoc, so `$` and backticks are
        # inert today. Assert anyway: unquoting the heredoc is a one-character edit, and
        # a `$` in a policy action would then expand to the empty string in the
        # customer's shell and silently narrow the policy they apply.
        for char in ("$", "`"):
            if char in rendered:
                raise DriftError(
                    f"rendered policy (compute_type={compute_type!r}) contains {char!r}, "
                    "which is unsafe to embed in a shell heredoc"
                )

        tokens = set(_PLACEHOLDER_RE.findall(rendered))
        unrecognized = tokens - {policy_data.ACCOUNT_ID_PLACEHOLDER}
        if unrecognized:
            raise DriftError(
                f"rendered policy (compute_type={compute_type!r}) contains unrecognized "
                f"placeholder(s) {sorted(unrecognized)}; only {policy_data.ACCOUNT_ID_PLACEHOLDER} "
                "is substituted by the script"
            )
        # The default (ecs_fargate) branch never runs the placeholder substitution, so a
        # placeholder in the base statements would ship into the customer's account verbatim.
        if compute_type is None and tokens:
            raise DriftError(
                f"the default policy document contains {policy_data.ACCOUNT_ID_PLACEHOLDER} "
                f"but the {policy_data.DEFAULT_COMPUTE_TYPE} branch never substitutes it"
            )

    for statement in policy_data.statements():
        if statement.get("Effect") != "Allow":
            raise DriftError(
                "policy.json contains a non-Allow statement; policy_data.grants() only "
                "models Allow and would report a denied action as granted"
            )

    current = policy_data.version()
    hashes = policy_data.version_hashes()
    if current < 1:
        raise DriftError(f"version must be >= 1, got {current}")
    # A freshly bumped version legitimately sits above every recorded one until --write
    # records its hash; going backwards would silently re-publish a released version.
    recorded = {int(v) for v in hashes}
    if recorded and current < max(recorded):
        raise DriftError(
            f"version {current} is below the highest recorded version {max(recorded)}; "
            "versions are append-only"
        )


def _assert_cfn_invariants() -> None:
    """CFN-specific checks, deliberately NOT a reuse of _assert_invariants' "no $ or
    backtick" rule — that's a bash-heredoc-safety check and would fail permanently on
    the eks template, which legitimately renders ${AWS::AccountId} inside every
    Fn::Sub."""
    for compute_type, path in CFN_TEMPLATE_PATHS.items():
        rendered = _cfn_template_json(compute_type)

        # Reuses the stray-placeholder check's intent (see _assert_invariants), run
        # against CFN output instead of the heredoc: every placeholder occurrence must
        # have been wrapped in an Fn::Sub, never left bare in the rendered JSON text.
        bare = re.findall(re.escape(policy_data.ACCOUNT_ID_PLACEHOLDER), rendered)
        if bare:
            raise DriftError(
                f"{_rel(path)}: {len(bare)} bare occurrence(s) of "
                f"{policy_data.ACCOUNT_ID_PLACEHOLDER} survived rendering; every occurrence "
                "must be wrapped in an Fn::Sub"
            )

        # Parity check: the default branch never substitutes, so its template must
        # carry zero Fn::Sub objects at all.
        if compute_type == "ecs_fargate" and "Fn::Sub" in rendered:
            raise DriftError(
                f"{_rel(path)}: the {policy_data.DEFAULT_COMPUTE_TYPE} template must contain no "
                "Fn::Sub objects — only the eks template's statements reference "
                "__LAUNCHPAD_ACCOUNT_ID__"
            )

        # Round-trip: unwrapping every Fn::Sub in the rendered PolicyDocument (back to
        # the literal placeholder) must reproduce policy_data.document(compute_type)'s
        # Statement list exactly — not just "every Fn::Sub value appears somewhere in
        # it" (set membership would miss a dropped statement or a mutated
        # Action/Effect/Condition), but byte-for-byte equal once the only designed
        # rendering difference — Resource/NotResource encoding — is reversed.
        template = json.loads(rendered)
        policy_statements = template["Resources"]["LaunchpadDeploymentPolicy"]["Properties"][
            "PolicyDocument"
        ]["Statement"]
        unwrapped = [unwrap_cfn_statement(s) for s in policy_statements]
        expected_statements = policy_data.document(compute_type)["Statement"]
        if unwrapped != expected_statements:
            raise DriftError(
                f"{_rel(path)}: the rendered PolicyDocument, with every Fn::Sub unwrapped "
                f"back to {policy_data.ACCOUNT_ID_PLACEHOLDER}, does not equal "
                f"policy_data.document({compute_type!r})['Statement']. Only Resource/"
                "NotResource encoding should ever differ between the two."
            )


def _unwrap_resource_field(value):
    if isinstance(value, list):
        return [_unwrap_resource_field(item) for item in value]
    if isinstance(value, dict) and "Fn::Sub" in value:
        return value["Fn::Sub"].replace(_CFN_ACCOUNT_ID_SUB, policy_data.ACCOUNT_ID_PLACEHOLDER)
    return value


def unwrap_cfn_statement(stmt: dict) -> dict:
    """Inverse of _cfn_statement: strips the Fn::Sub wrapping back to the literal
    __LAUNCHPAD_ACCOUNT_ID__ placeholder. Exposed (not underscore-prefixed) for the
    round-trip test to reuse rather than reimplement."""
    unwrapped = dict(stmt)
    for key in ("Resource", "NotResource"):
        if key in unwrapped:
            unwrapped[key] = _unwrap_resource_field(unwrapped[key])
    return unwrapped


def _check_version_binding() -> None:
    current = policy_data.version()
    expected = policy_data.version_hashes().get(str(current))
    actual = policy_data.statements_hash()
    if expected == "PLACEHOLDER" or expected is None:
        raise DriftError(
            f"version {current} has no recorded hash. Run this script with --write to record it."
        )
    if expected != actual:
        raise DriftError(
            f"the statements in policy.json changed but version is still {current}.\n"
            f"  recorded hash for v{current}: {expected}\n"
            f"  hash of current statements:  {actual}\n"
            f"Bump \"version\" to {current + 1} and re-run with --write. Customers onboarded "
            "at an older version need the Refresh policy script before the new grants exist "
            "in their account."
        )


def _check_document_hashes() -> None:
    current = str(policy_data.version())
    recorded = policy_data.document_hashes().get(current, {})
    compute_types = {policy_data.DEFAULT_COMPUTE_TYPE, *policy_data.compute_type_statements()}
    for compute_type in sorted(compute_types):
        if compute_type not in recorded:
            raise DriftError(
                f'document_hashes["{current}"] has no entry for compute_type '
                f"{compute_type!r}. Run this script with --write to record it."
            )
        actual = policy_data.document_hash(compute_type)
        if recorded[compute_type] != actual:
            raise DriftError(
                f"the {compute_type!r} document changed but document_hashes[\"{current}\"]"
                f'["{compute_type}"] still records the old hash.\n'
                f"  recorded: {recorded[compute_type]}\n"
                f"  actual:   {actual}\n"
                "Run this script with --write to record it."
            )


def _record_document_hashes() -> bool:
    """Bind the current version's rendered document hash for every compute type.
    Returns True if policy.json changed. Never touches an older version's entry — v1
    stays exactly as seeded."""
    raw = json.loads(policy_data.POLICY_PATH.read_text())
    current = str(raw["version"])
    compute_types = {policy_data.DEFAULT_COMPUTE_TYPE, *policy_data.compute_type_statements()}
    current_entry = raw.setdefault("document_hashes", {}).setdefault(current, {})

    changed = False
    for compute_type in compute_types:
        actual = policy_data.document_hash(compute_type)
        if current_entry.get(compute_type) != actual:
            current_entry[compute_type] = actual
            changed = True
    if not changed:
        return False
    policy_data.POLICY_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    policy_data.load.cache_clear()
    return True


def _record_version_hash() -> bool:
    """Bind the current version to the current statements. Returns True if policy.json changed."""
    raw = json.loads(policy_data.POLICY_PATH.read_text())
    current = str(raw["version"])
    actual = policy_data.statements_hash()
    recorded = raw["version_hashes"].get(current)
    if recorded == actual:
        return False
    if recorded not in (None, "PLACEHOLDER"):
        raise DriftError(
            f"version {current} is already published with a different permission set. "
            f'Bump "version" to {int(current) + 1} instead of editing a released version.'
        )
    raw["version_hashes"][current] = actual
    policy_data.POLICY_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    policy_data.load.cache_clear()
    return True


def _render_all() -> dict[Path, str]:
    """Render every region. Grouped by file so a file carrying two regions is read once,
    and done before anything is written so a missing marker aborts with nothing touched."""
    rendered = {}
    for path in dict.fromkeys(path for path, _, _, _ in REGIONS):
        updated = path.read_text()
        for region_path, comment, name, renderer in REGIONS:
            if region_path == path:
                updated = _replace_region(updated, comment, name, renderer(), path)
        rendered[path] = updated
    return rendered


def _render_cfn_templates() -> dict[Path, str]:
    """Whole generated files, not regions — see CFN_TEMPLATE_PATHS."""
    return {path: _cfn_template_json(compute_type) for compute_type, path in CFN_TEMPLATE_PATHS.items()}


def run(write: bool) -> int:
    _assert_invariants()
    _assert_cfn_invariants()
    rendered = _render_all()
    rendered.update(_render_cfn_templates())

    if not write:
        _check_version_binding()
        _check_document_hashes()
        for path, updated in rendered.items():
            current = path.read_text() if path.exists() else None
            if updated != current:
                raise DriftError(
                    f"{_rel(path)} is out of date with {SOURCE_HINT}.\n"
                    f"Run: python {_rel(Path(__file__).resolve())} --write"
                )
        print(f"IAM policy v{policy_data.version()}: all generated regions are up to date.")
        return 0

    changed = []
    if _record_version_hash():
        changed.append(policy_data.POLICY_PATH)
    if _record_document_hashes() and policy_data.POLICY_PATH not in changed:
        changed.append(policy_data.POLICY_PATH)
    _check_version_binding()
    _check_document_hashes()
    for path, updated in rendered.items():
        current = path.read_text() if path.exists() else None
        if updated != current:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(updated)
            changed.append(path)

    for path in changed:
        print(f"wrote {_rel(path)}")
    if not changed:
        print("no changes")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="fail if generated output drifted")
    group.add_argument("--write", action="store_true", help="regenerate from policy.json")
    args = parser.parse_args(argv)

    try:
        return run(write=args.write)
    except DriftError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
