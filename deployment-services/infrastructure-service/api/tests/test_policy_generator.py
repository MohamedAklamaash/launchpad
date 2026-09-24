"""The IAM policy source-of-truth gate.

The policy is a security boundary that customers' reviewers read in
`docs/IAM_POLICIES.md` and that customers' shells apply from `create_aws_role.sh`.
Before this, those were three hand-maintained copies. These tests assert the copies are
generated, that a hand-edit fails, and that widening the grants without bumping the
version fails — because `Infrastructure.policy_version` is a lie otherwise.
"""

import json
import shutil

import pytest
from api.cloud_providers.aws import iam_policy
from api.cloud_providers.aws.iam_policy import generate, policy_data


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A throwaway copy of every file the generator owns, so a failing assertion can
    never leave the real script or docs half-rendered."""
    script = tmp_path / "create_aws_role.sh"
    docs = tmp_path / "IAM_POLICIES.md"
    policy = tmp_path / "policy.json"
    shutil.copy(generate.SCRIPT_PATH, script)
    shutil.copy(generate.DOCS_PATH, docs)
    shutil.copy(policy_data.POLICY_PATH, policy)

    redirect = {generate.SCRIPT_PATH: script, generate.DOCS_PATH: docs}
    monkeypatch.setattr(generate, "REGIONS", [
        (redirect[path], comment, name, renderer)
        for path, comment, name, renderer in generate.REGIONS
    ])
    monkeypatch.setattr(generate, "SCRIPT_PATH", script)
    monkeypatch.setattr(generate, "DOCS_PATH", docs)
    monkeypatch.setattr(policy_data, "POLICY_PATH", policy)
    policy_data.load.cache_clear()
    yield type("Sandbox", (), {"script": script, "docs": docs, "policy": policy})
    policy_data.load.cache_clear()


def _rewrite_policy(path, **changes):
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    policy_data.load.cache_clear()


def test_committed_output_matches_policy_json():
    """The gate CI runs. Red here means someone hand-edited a generated region."""
    assert generate.run(write=False) == 0


def test_policy_json_is_valid_and_versioned():
    document = iam_policy.document()
    assert document["Version"] == "2012-10-17"
    assert document["Statement"], "policy must grant something"
    assert iam_policy.version() >= 1
    assert json.loads(iam_policy.document_json()) == document


def test_current_version_is_bound_to_current_statements():
    recorded = policy_data.version_hashes()[str(iam_policy.version())]
    assert recorded == iam_policy.statements_hash()


def test_every_prechecked_action_is_actually_granted():
    """`iam_precheck` simulates these against the customer's role. If the policy stopped
    granting one, every customer would fail the precheck with a refresh that can't fix it."""
    from api.cloud_providers.aws.iam_precheck import _CREATE_ACTIONS_BY_ENGINE

    for engine, actions in _CREATE_ACTIONS_BY_ENGINE.items():
        for action in actions:
            assert iam_policy.grants(action), f"{engine}: policy does not grant {action}"


def test_grants_rejects_actions_outside_the_policy():
    assert not iam_policy.grants("acm:RequestCertificate")
    assert not iam_policy.grants("ce:GetCostAndUsage")


def test_hand_editing_a_generated_region_fails_the_check(sandbox):
    sandbox.script.write_text(
        sandbox.script.read_text().replace('"codebuild:*",', '"codebuild:*",\n        "acm:*",')
    )
    with pytest.raises(generate.DriftError, match="out of date"):
        generate.run(write=False)


def test_removing_the_markers_fails_rather_than_silently_skipping(sandbox):
    sandbox.docs.write_text(sandbox.docs.read_text().replace("<!-- END GENERATED -->", ""))
    with pytest.raises(generate.DriftError, match="expected exactly one region"):
        generate.run(write=False)


def test_widening_the_policy_without_bumping_the_version_fails(sandbox):
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements[0]["Action"].append("acm:*")
    _rewrite_policy(sandbox.policy, statements=statements)

    with pytest.raises(generate.DriftError, match="version is still 1"):
        generate.run(write=False)


def test_bumping_the_version_records_the_hash_and_renders_everywhere(sandbox):
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements[0]["Action"].append("acm:*")
    _rewrite_policy(sandbox.policy, statements=statements, version=2)

    assert generate.run(write=True) == 0

    assert json.loads(sandbox.policy.read_text())["version_hashes"]["2"] == policy_data.statements_hash()
    assert "POLICY_VERSION=2" in sandbox.script.read_text()
    assert sandbox.script.read_text().count('"acm:*"') == 1
    assert sandbox.docs.read_text().count('"acm:*"') == 2
    assert generate.run(write=False) == 0


def test_narrowing_a_released_version_in_place_is_refused(sandbox):
    """A customer already ran v1. Silently redefining what v1 grants would leave their
    account holding permissions Launchpad no longer believes it asked for."""
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements[0]["Action"].remove("s3:*")
    _rewrite_policy(sandbox.policy, statements=statements)

    with pytest.raises(generate.DriftError, match="already published"):
        generate.run(write=True)
    with pytest.raises(generate.DriftError, match="version is still 1"):
        generate.run(write=False)


def test_a_deny_statement_is_refused_because_grants_cannot_model_it(sandbox):
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements.append({"Effect": "Deny", "Action": "s3:DeleteBucket", "Resource": "*"})
    _rewrite_policy(sandbox.policy, statements=statements, version=2)

    with pytest.raises(generate.DriftError, match="non-Allow statement"):
        generate.run(write=False)


def test_shell_metacharacters_in_the_policy_are_refused(sandbox):
    """The heredoc is quoted so `$` is inert today; unquoting it is a one-character edit
    that would silently blank an action in the customer's shell."""
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements[0]["Action"].append("s3:${EVIL}")
    _rewrite_policy(sandbox.policy, statements=statements, version=2)

    with pytest.raises(generate.DriftError, match="unsafe to embed in a shell heredoc"):
        generate.run(write=False)


def test_script_heredoc_is_quoted_so_the_policy_is_never_expanded():
    assert "cat > \"$WORK_DIR/launchpad-policy.json\" <<'EOF'" in generate.SCRIPT_PATH.read_text()


def test_script_reports_the_current_version_on_both_callbacks():
    script = generate.SCRIPT_PATH.read_text()
    assert f"POLICY_VERSION={iam_policy.version()}" in script
    assert script.count('\\"policy_version\\":${POLICY_VERSION}') == 2
