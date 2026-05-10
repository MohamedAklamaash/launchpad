"""Tests for ``_check_policy_sync.py`` — the CI guard that keeps the
LaunchpadDeploymentPolicy in sync between ``create_aws_role.sh`` and
``update_aws_role.sh``.

Strategy
--------
The guard hard-codes the script paths next to itself (``SCRIPT_DIR / ...``)
so to test failure cases we copy the whole ``app_scripts/`` tree to a tmp
directory, mutate the *copy* of ``update_aws_role.sh``, and run the *copy*
of ``_check_policy_sync.py`` against it. That keeps the test hermetic — we
never modify the real scripts in-tree.

We invoke the guard as a subprocess (rather than ``import``-and-call) so we
exercise the same code path CI runs and capture stdout/stderr the same way.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_APP_SCRIPTS = Path(__file__).resolve().parent


def _copy_scripts(dst: Path) -> Path:
    """Copy the real app_scripts dir to ``dst`` and return the copied dir."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in (
        "_check_policy_sync.py",
        "create_aws_role.sh",
        "update_aws_role.sh",
    ):
        shutil.copy(REPO_APP_SCRIPTS / name, dst / name)
    return dst


def _run_guard(scripts_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(scripts_dir / "_check_policy_sync.py")],
        capture_output=True,
        text=True,
        check=False,
    )


def test_happy_path_real_scripts_pass() -> None:
    """The current in-tree scripts must pass the guard."""
    result = subprocess.run(
        [sys.executable, str(REPO_APP_SCRIPTS / "_check_policy_sync.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Guard failed on unmodified scripts. stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "OK:" in result.stdout
    assert "create_aws_role.sh" in result.stdout
    assert "update_aws_role.sh" in result.stdout


def test_removed_action_in_update_fails(tmp_path: Path) -> None:
    """If update_aws_role.sh drops an action create_aws_role.sh still lists,
    the guard must fail and name the missing action.

    This is the exact regression that broke ``create_project`` historically:
    ``codebuild:*`` was present in create_aws_role.sh but missing from
    update_aws_role.sh.
    """
    scripts_dir = _copy_scripts(tmp_path / "app_scripts")
    update_path = scripts_dir / "update_aws_role.sh"
    contents = update_path.read_text()
    # Drop the codebuild:* action line entirely from the heredoc in the
    # *copy*. The action appears as a JSON string in the Action list, so we
    # need to also rebalance the surrounding commas. Easiest: remove the
    # entire "codebuild:*" line (whether or not it has a trailing comma) AND
    # the preceding comma on the previous line if codebuild was last.
    #
    # The current real script has codebuild:* as the LAST entry (no trailing
    # comma), so we strip both the line and the trailing comma after dynamodb:*.
    mutated = re.sub(
        r',\n\s*"codebuild:\*"',
        '',
        contents,
        count=1,
    )
    assert mutated != contents, "Failed to mutate update_aws_role.sh copy"
    update_path.write_text(mutated)

    result = _run_guard(scripts_dir)
    assert result.returncode != 0, (
        f"Expected non-zero exit; got 0. stdout={result.stdout!r}"
    )
    assert "FAIL" in result.stdout
    assert "Missing in update_aws_role.sh" in result.stdout
    assert "codebuild:*" in result.stdout


def test_extra_action_in_update_fails(tmp_path: Path) -> None:
    """If update_aws_role.sh adds an action create_aws_role.sh does not have,
    the guard must fail and name the extra action."""
    scripts_dir = _copy_scripts(tmp_path / "app_scripts")
    update_path = scripts_dir / "update_aws_role.sh"
    contents = update_path.read_text()
    # Inject a bogus extra action into the first Statement's Action array.
    # codebuild:* is the LAST entry (no trailing comma), so we append a new
    # action by replacing it with itself + a comma + a new line.
    needle = '"codebuild:*"'
    assert needle in contents, "Anchor line missing — update test"
    mutated = contents.replace(
        needle,
        '"codebuild:*",\n        "BOGUS_EXTRA_ACTION:*"',
        1,
    )
    update_path.write_text(mutated)

    result = _run_guard(scripts_dir)
    assert result.returncode != 0, (
        f"Expected non-zero exit; got 0. stdout={result.stdout!r}"
    )
    assert "FAIL" in result.stdout
    assert "Extra in update_aws_role.sh" in result.stdout
    assert "BOGUS_EXTRA_ACTION:*" in result.stdout


def test_malformed_heredoc_json_fails(tmp_path: Path) -> None:
    """If the embedded JSON becomes unparseable, the guard must surface the
    error rather than silently succeeding."""
    scripts_dir = _copy_scripts(tmp_path / "app_scripts")
    update_path = scripts_dir / "update_aws_role.sh"
    contents = update_path.read_text()
    # Break JSON by removing a closing bracket of the Action array. We replace
    # the first occurrence of ``"codebuild:*"\n    ],`` style ending. Easier:
    # corrupt by replacing the Version field with an unquoted value.
    mutated = contents.replace('"Version": "2012-10-17"', '"Version": 2012-10-17', 1)
    assert mutated != contents
    update_path.write_text(mutated)

    result = _run_guard(scripts_dir)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "not valid JSON" in combined or "JSONDecodeError" in combined


@pytest.mark.parametrize("script_name", ["create_aws_role.sh", "update_aws_role.sh"])
def test_missing_heredoc_fails(tmp_path: Path, script_name: str) -> None:
    """If the policy heredoc is removed entirely, the guard must complain
    rather than reporting an empty set match."""
    scripts_dir = _copy_scripts(tmp_path / "app_scripts")
    target = scripts_dir / script_name
    # Replace the heredoc anchor so the regex no longer matches.
    contents = target.read_text()
    mutated = contents.replace('launchpad-policy.json', 'some-other-file.json')
    assert mutated != contents
    target.write_text(mutated)

    result = _run_guard(scripts_dir)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "could not locate" in combined
