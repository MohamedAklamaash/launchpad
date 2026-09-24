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
    current = policy_data.version()
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements[0]["Action"].append("acm:*")
    _rewrite_policy(sandbox.policy, statements=statements)

    with pytest.raises(generate.DriftError, match=f"version is still {current}"):
        generate.run(write=False)


def test_bumping_the_version_records_the_hash_and_renders_everywhere(sandbox):
    next_version = policy_data.version() + 1
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements[0]["Action"].append("acm:*")
    _rewrite_policy(sandbox.policy, statements=statements, version=next_version)

    assert generate.run(write=True) == 0

    assert (
        json.loads(sandbox.policy.read_text())["version_hashes"][str(next_version)]
        == policy_data.statements_hash()
    )
    assert f"POLICY_VERSION={next_version}" in sandbox.script.read_text()
    assert sandbox.script.read_text().count('"acm:*"') == 2
    assert sandbox.docs.read_text().count('"acm:*"') == 2
    assert generate.run(write=False) == 0


def test_narrowing_a_released_version_in_place_is_refused(sandbox):
    """A customer already ran the current version. Silently redefining what it grants
    would leave their account holding permissions Launchpad no longer believes it asked
    for."""
    current = policy_data.version()
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements[0]["Action"].remove("s3:*")
    _rewrite_policy(sandbox.policy, statements=statements)

    with pytest.raises(generate.DriftError, match="already published"):
        generate.run(write=True)
    with pytest.raises(generate.DriftError, match=f"version is still {current}"):
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


def test_default_document_is_unchanged_by_the_eks_addition():
    """An existing golden expectation depends on document() with no argument staying
    exactly the ECS/Fargate-only statement list, with no EKS grants mixed in."""
    document = policy_data.document()
    assert document["Statement"] == policy_data.statements()
    assert all(statement["Effect"] == "Allow" for statement in document["Statement"])
    assert "eks:" not in policy_data.document_json()


def test_eks_document_appends_the_eks_statements():
    base = policy_data.document()["Statement"]
    eks = policy_data.document("eks")["Statement"]
    extras = policy_data.compute_type_statements()["eks"]

    assert eks == base + extras
    assert eks[-1]["Effect"] == "Deny"


def test_eks_policy_is_valid_json_after_placeholder_substitution():
    rendered = policy_data.document_json("eks")
    assert policy_data.ACCOUNT_ID_PLACEHOLDER in rendered

    deny = policy_data.document("eks")["Statement"][-1]
    assert deny["Effect"] == "Deny"
    assert all(policy_data.ACCOUNT_ID_PLACEHOLDER in arn for arn in deny["NotResource"])

    substituted = rendered.replace(policy_data.ACCOUNT_ID_PLACEHOLDER, "123456789012")
    document = json.loads(substituted)
    assert document["Version"] == "2012-10-17"
    assert policy_data.ACCOUNT_ID_PLACEHOLDER not in json.dumps(document)


def test_rendered_policies_contain_no_shell_metacharacters():
    for compute_type in [None, *policy_data.compute_type_statements()]:
        rendered = policy_data.document_json(compute_type)
        assert "$" not in rendered
        assert "`" not in rendered


def test_unknown_placeholder_is_rejected(sandbox):
    data = json.loads(sandbox.policy.read_text())
    data["compute_type_statements"]["eks"][0]["Resource"] = "arn:aws:eks:*:__SOME_OTHER_TOKEN__:*"
    _rewrite_policy(sandbox.policy, compute_type_statements=data["compute_type_statements"])

    with pytest.raises(generate.DriftError, match="unrecognized"):
        generate.run(write=False)


def test_placeholder_in_the_base_statements_is_rejected(sandbox):
    """The ecs_fargate branch never runs the placeholder substitution, so a placeholder
    left in the base statements would ship into the customer's account verbatim."""
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements[0]["Resource"] = f"arn:aws:s3:::{policy_data.ACCOUNT_ID_PLACEHOLDER}-bucket"
    _rewrite_policy(sandbox.policy, statements=statements)

    with pytest.raises(generate.DriftError, match="never substitutes it"):
        generate.run(write=False)


def test_editing_eks_statements_without_a_version_bump_fails(sandbox):
    """Mirrors test_widening_the_policy_without_bumping_the_version_fails, but for the
    compute-type extras: statements_hash() must cover them too, or this drift would pass
    the gate silently."""
    current = policy_data.version()
    data = json.loads(sandbox.policy.read_text())
    data["compute_type_statements"]["eks"][0]["Action"].append("eks:TagResource")
    _rewrite_policy(sandbox.policy, compute_type_statements=data["compute_type_statements"])

    with pytest.raises(generate.DriftError, match=f"version is still {current}"):
        generate.run(write=False)


def test_script_selects_the_policy_by_compute_type():
    script = generate.SCRIPT_PATH.read_text()
    assert 'case "$COMPUTE_TYPE" in' in script
    assert f"  {policy_data.DEFAULT_COMPUTE_TYPE})" in script
    assert "  eks)" in script
    assert 'cat > "$WORK_DIR/launchpad-policy.eks.json" <<\'EOF\'' in script
    assert (
        f'sed "s/{policy_data.ACCOUNT_ID_PLACEHOLDER}/${{ACCOUNT_ID}}/g" '
        '"$WORK_DIR/launchpad-policy.eks.json" > "$WORK_DIR/launchpad-policy.json"'
    ) in script
    assert "unknown LAUNCHPAD_COMPUTE_TYPE" in script


def test_script_default_compute_type_matches_the_generator_default():
    script = generate.SCRIPT_PATH.read_text()
    assert f'COMPUTE_TYPE="${{LAUNCHPAD_COMPUTE_TYPE:-{policy_data.DEFAULT_COMPUTE_TYPE}}}"' in script
