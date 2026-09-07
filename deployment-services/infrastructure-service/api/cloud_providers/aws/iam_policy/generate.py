"""Render every customer-facing copy of the deployment policy from `policy.json`.

    python .../iam_policy/generate.py --check    # CI gate: fail if committed output drifted
    python .../iam_policy/generate.py --write    # regenerate after editing policy.json

The policy used to exist only as a bash heredoc, hand-duplicated twice in
`docs/IAM_POLICIES.md`. Three copies of a security boundary is three chances to drift,
and a drifted doc is what a customer's security reviewer reads. Now the heredoc and both
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
        'cat > "$WORK_DIR/launchpad-policy.json" <<\'EOF\'',
        policy_data.document_json(),
        "EOF",
    ]
    return "\n".join(lines)


def _docs_json_region() -> str:
    return "```json\n" + policy_data.document_json() + "\n```"


def _docs_cli_region() -> str:
    return "cat > deployment-policy.json <<'EOF'\n" + policy_data.document_json() + "\nEOF"


def _docs_version_region() -> str:
    return f"**Policy version**: {policy_data.version()}"


# (path, marker name, renderer). Marker names are the literal text between the comment
# delimiters, so a grep for "BEGIN GENERATED" finds every region this file owns.
REGIONS = [
    (SCRIPT_PATH, "#", "deployment policy", _script_region),
    (DOCS_PATH, "<!--", "deployment policy", _docs_json_region),
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
    rendered = policy_data.document_json()

    # The script writes this JSON through a quoted heredoc, so `$` and backticks are
    # inert today. Assert anyway: unquoting the heredoc is a one-character edit, and a
    # `$` in a policy action would then expand to the empty string in the customer's
    # shell and silently narrow the policy they apply.
    for char in ("$", "`"):
        if char in rendered:
            raise DriftError(
                f"rendered policy contains {char!r}, which is unsafe to embed in a shell heredoc"
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


def run(write: bool) -> int:
    _assert_invariants()
    rendered = _render_all()

    if not write:
        _check_version_binding()
        for path, updated in rendered.items():
            if updated != path.read_text():
                raise DriftError(
                    f"{_rel(path)} is out of date with {SOURCE_HINT}.\n"
                    f"Run: python {_rel(Path(__file__).resolve())} --write"
                )
        print(f"IAM policy v{policy_data.version()}: all generated regions are up to date.")
        return 0

    changed = []
    if _record_version_hash():
        changed.append(policy_data.POLICY_PATH)
    _check_version_binding()
    for path, updated in rendered.items():
        if updated != path.read_text():
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
