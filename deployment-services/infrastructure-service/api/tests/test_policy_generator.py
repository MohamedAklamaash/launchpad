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
    never leave the real script, docs, or CFN templates half-rendered — and so a
    --write test never overwrites the real committed CFN templates with a test's
    injected policy change."""
    script = tmp_path / "create_aws_role.sh"
    docs = tmp_path / "IAM_POLICIES.md"
    policy = tmp_path / "policy.json"
    cfn_dir = tmp_path / "cloudformation"
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
    monkeypatch.setattr(generate, "CFN_TEMPLATE_PATHS", {
        "ecs_fargate": cfn_dir / "launchpad-deployment-role.ecs-fargate.template.json",
        "eks": cfn_dir / "launchpad-deployment-role.eks.template.json",
    })
    monkeypatch.setattr(policy_data, "POLICY_PATH", policy)
    policy_data.load.cache_clear()
    yield type("Sandbox", (), {"script": script, "docs": docs, "policy": policy, "cfn_dir": cfn_dir})
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
    assert not iam_policy.grants("sns:Publish")
    assert not iam_policy.grants("route53:ChangeResourceRecordSets")


def test_v4_grants_the_acm_actions():
    """v4 (F1b TLS activation) adds ACM certificate lifecycle grants."""
    assert iam_policy.grants("acm:RequestCertificate")
    assert iam_policy.grants("acm:DescribeCertificate")
    assert iam_policy.grants("acm:ListCertificates")
    assert iam_policy.grants("acm:ListTagsForCertificate")
    assert iam_policy.grants("acm:AddTagsToCertificate")
    assert iam_policy.grants("acm:DeleteCertificate")


def test_acm_add_tags_and_delete_are_conditioned_not_unconditional():
    """Security review R3: acm:AddTagsToCertificate must never be a blank grant — an
    unconditioned tag-write lets this role tag any certificate in the account
    ManagedBy=launchpad and then delete it under acm:DeleteCertificate's own condition.
    grants() doesn't model conditions (by design — see its docstring), so this test reads
    policy_data.statements() directly."""
    def _find(action):
        for statement in policy_data.statements():
            actions = statement["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            if action in actions:
                return statement
        raise AssertionError(f"no statement grants {action}")

    add_tags = _find("acm:AddTagsToCertificate")
    assert add_tags["Condition"]["StringEquals"]["aws:RequestTag/ManagedBy"] == "launchpad"
    assert add_tags["Condition"]["ForAllValues:StringEquals"]["aws:TagKeys"] == ["ManagedBy"]

    delete = _find("acm:DeleteCertificate")
    assert delete["Condition"]["StringEquals"]["aws:ResourceTag/ManagedBy"] == "launchpad"

    request = _find("acm:RequestCertificate")
    assert request["Condition"]["StringEquals"]["aws:RequestTag/ManagedBy"] == "launchpad"


def test_v8_narrows_codebuild_to_delete_capable_actions():
    """F7 ticket 10: Launchpad builds every image on its own infrastructure now, never
    inside the customer's account — codebuild:* narrows to exactly what Nuke needs to
    clean up a legacy per-infra project, nothing that creates, starts, or updates one."""
    assert iam_policy.grants("codebuild:BatchGetProjects")
    assert iam_policy.grants("codebuild:DeleteProject")
    assert not iam_policy.grants("codebuild:CreateProject")
    assert not iam_policy.grants("codebuild:UpdateProject")
    assert not iam_policy.grants("codebuild:StartBuild")
    assert not iam_policy.grants("codebuild:BatchGetBuilds")


def test_v3_grants_the_cost_explorer_actions():
    """v3 adds account-wide Cost Explorer read + tag-activation grants for F4."""
    assert iam_policy.grants("ce:GetCostAndUsage")
    assert iam_policy.grants("ce:UpdateCostAllocationTagsStatus")
    assert iam_policy.grants("ce:ListCostAllocationTags")


def test_hand_editing_a_generated_region_fails_the_check(sandbox):
    sandbox.script.write_text(
        sandbox.script.read_text().replace('"ecr:*",', '"ecr:*",\n        "acm:*",')
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


# ── per-compute-type staleness ────────────────────────────────────────────────

def test_document_hash_of_ecs_fargate_equals_the_default_document():
    """Pins the contract the per-compute-type staleness design rests on: naming
    "ecs_fargate" explicitly renders the same document as passing nothing."""
    assert policy_data.document_hash("ecs_fargate") == policy_data.document_hash(None)


def test_required_version_for_matches_committed_data():
    """v6 (Nuke infrastructure verification) added a base-statement grant
    (tag:GetResources); v7 (pod identity for the CloudWatch observability add-on) only
    touched compute_type_statements["eks"]. v8 (F7 ticket 10, platform-side builds)
    narrowed the base-statement codebuild:* grant to delete-capable actions only, which
    changes every compute type's rendered document. v9 (F8 ticket 1, CloudFormation
    onboarding) adds another base-statement grant (cloudformation:*) — both now require
    v9."""
    assert policy_data.required_version_for("ecs_fargate") == 9
    assert policy_data.required_version_for("eks") == 9


def test_check_fails_when_a_compute_type_is_missing_from_document_hashes(sandbox):
    data = json.loads(sandbox.policy.read_text())
    del data["document_hashes"][str(data["version"])]["eks"]
    _rewrite_policy(sandbox.policy, document_hashes=data["document_hashes"])

    with pytest.raises(generate.DriftError, match="run --write|--write to record it"):
        generate.run(write=False)


def test_bumping_over_a_changed_base_statement_raises_the_required_version_for_every_type(sandbox):
    """A base-statement change affects every compute_type's rendered document, so the
    requirement moves for ecs_fargate and eks alike."""
    next_version = policy_data.version() + 1
    statements = json.loads(sandbox.policy.read_text())["statements"]
    statements[0]["Action"].append("acm:*")
    _rewrite_policy(sandbox.policy, statements=statements, version=next_version)

    assert generate.run(write=True) == 0

    assert policy_data.required_version_for("ecs_fargate") == next_version
    assert policy_data.required_version_for("eks") == next_version


_CFN_GRANTED_ACTIONS = {
    "cloudformation:DescribeStacks",
    "cloudformation:DescribeStackEvents",
    "cloudformation:DescribeStackResources",
    "cloudformation:GetTemplate",
    "cloudformation:GetTemplateSummary",
    "cloudformation:CreateChangeSet",
    "cloudformation:DescribeChangeSet",
    "cloudformation:ListChangeSets",
    "cloudformation:DeleteChangeSet",
    "cloudformation:UpdateStack",
}


def test_v9_adds_an_explicit_cloudformation_grant_not_a_wildcard():
    """F8 ticket 1: the CFN grant is an explicit action list, not cloudformation:*, on
    stack/*/* (both account and stack name wildcarded — the quick-create link lets the
    customer rename the suggested stack before creating it, so narrowing by name is
    unenforceable). cloudformation:* on every stack reads to a customer's security
    review as "Launchpad can delete any CloudFormation stack in my account", which it
    cannot do — DeleteStack, CreateStack, and ExecuteChangeSet are deliberately
    excluded (see plan/F8-cloudformation-onboarding.md's "last-infra teardown" section
    for why DeleteStack specifically is never called)."""
    for action in _CFN_GRANTED_ACTIONS:
        assert iam_policy.grants(action), f"expected {action} to be granted"

    for statement in policy_data.statements():
        actions = statement["Action"]
        actions = [actions] if isinstance(actions, str) else actions
        cfn_actions = [a for a in actions if a.startswith("cloudformation:")]
        if not cfn_actions:
            continue
        assert statement["Resource"] == "arn:aws:cloudformation:*:*:stack/*/*"
        assert set(cfn_actions) == _CFN_GRANTED_ACTIONS
        return
    raise AssertionError("no statement grants any cloudformation: action")


def test_cloudformation_wildcard_and_delete_stack_are_never_granted():
    assert not iam_policy.grants("cloudformation:*")
    assert not iam_policy.grants("cloudformation:DeleteStack")
    assert not iam_policy.grants("cloudformation:CreateStack")
    assert not iam_policy.grants("cloudformation:ExecuteChangeSet")

    for statement in policy_data.statements():
        actions = statement["Action"]
        actions = [actions] if isinstance(actions, str) else actions
        assert "cloudformation:*" not in actions
        assert "cloudformation:DeleteStack" not in actions
        assert "cloudformation:CreateStack" not in actions
        assert "cloudformation:ExecuteChangeSet" not in actions


# ── CloudFormation template generation (F8 ticket 1) ──────────────────────────


def _cfn_policy_statements(template: dict) -> list[dict]:
    return template["Resources"]["LaunchpadDeploymentPolicy"]["Properties"]["PolicyDocument"]["Statement"]


def _cfn_resource_values(statement: dict, key: str) -> list:
    value = statement.get(key)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def test_committed_cfn_templates_exist_and_are_valid_json():
    for path in generate.CFN_TEMPLATE_PATHS.values():
        template = json.loads(path.read_text())
        assert template["AWSTemplateFormatVersion"] == "2010-09-09"
        assert "LaunchpadDeploymentRole" in template["Resources"]
        assert "LaunchpadDeploymentPolicy" in template["Resources"]


def test_ecs_fargate_template_has_no_fn_sub_and_no_placeholder():
    """Parity check for 'the default branch never substitutes': the ecs_fargate
    template is the one CFN never needs AWS::AccountId for, so it must carry zero
    Fn::Sub objects at all, not just zero inside the policy document."""
    rendered = generate.CFN_TEMPLATE_PATHS["ecs_fargate"].read_text()
    assert "Fn::Sub" not in rendered
    assert policy_data.ACCOUNT_ID_PLACEHOLDER not in rendered


def test_eks_template_uses_fn_sub_and_leaves_no_bare_placeholder():
    rendered = generate.CFN_TEMPLATE_PATHS["eks"].read_text()
    assert "Fn::Sub" in rendered
    assert policy_data.ACCOUNT_ID_PLACEHOLDER not in rendered


def test_zero_stray_placeholder_survives_rendering(sandbox):
    """_cfn_statement only wraps Resource/NotResource; a placeholder anywhere else in a
    future statement (e.g. an Action) would ship unwrapped into the CFN template."""
    data = json.loads(sandbox.policy.read_text())
    data["compute_type_statements"]["eks"][0]["Action"].append(
        f"eks:{policy_data.ACCOUNT_ID_PLACEHOLDER}Thing"
    )
    _rewrite_policy(sandbox.policy, compute_type_statements=data["compute_type_statements"])

    with pytest.raises(generate.DriftError, match="bare occurrence"):
        generate.run(write=False)


def test_cfn_fn_sub_round_trips_to_policy_data_document():
    """Unwrapping every Fn::Sub in the rendered PolicyDocument back to the literal
    placeholder must reproduce policy_data.document(compute_type)['Statement']
    exactly — a stronger guarantee than "every value appears somewhere in it": a
    dropped statement or a mutated Action/Effect/Condition would fail this, not just
    a mismatched Resource/NotResource."""
    fn_sub_seen = {"ecs_fargate": 0, "eks": 0}
    for compute_type, path in generate.CFN_TEMPLATE_PATHS.items():
        template = json.loads(path.read_text())
        policy_statements = _cfn_policy_statements(template)
        unwrapped = [generate.unwrap_cfn_statement(s) for s in policy_statements]
        assert unwrapped == policy_data.document(compute_type)["Statement"]

        for statement in policy_statements:
            for key in ("Resource", "NotResource"):
                for value in _cfn_resource_values(statement, key):
                    if isinstance(value, dict):
                        fn_sub_seen[compute_type] += 1

    assert fn_sub_seen["eks"] > 0, "eks template should wrap at least one resource string"
    assert fn_sub_seen["ecs_fargate"] == 0


def test_cfn_check_write_round_trip(sandbox):
    """Mirrors the script/docs --check/--write round trip, for whole generated files
    instead of delimited regions: a from-scratch write is reproduced exactly by --check."""
    for path in generate.CFN_TEMPLATE_PATHS.values():
        assert not path.exists()

    assert generate.run(write=True) == 0
    for path in generate.CFN_TEMPLATE_PATHS.values():
        assert path.exists()

    assert generate.run(write=False) == 0
    assert generate.run(write=True) == 0  # idempotent: nothing left to write


def test_cfn_templates_carry_the_four_role_tags():
    for compute_type, path in generate.CFN_TEMPLATE_PATHS.items():
        template = json.loads(path.read_text())
        tags = {
            tag["Key"]: tag["Value"]
            for tag in template["Resources"]["LaunchpadDeploymentRole"]["Properties"]["Tags"]
        }
        assert tags["launchpad:managed-by"] == "cloudformation"
        assert tags["launchpad:policy-version"] == str(policy_data.version())
        assert tags["launchpad:cfn-stack-name"] == {"Ref": "AWS::StackName"}
        expected_compute_types = "ecs_fargate+eks" if compute_type == "eks" else "ecs_fargate"
        assert tags["launchpad:compute-types"] == expected_compute_types


def test_eks_cfn_template_sets_max_session_duration_ecs_fargate_does_not():
    eks = json.loads(generate.CFN_TEMPLATE_PATHS["eks"].read_text())
    ecs_fargate = json.loads(generate.CFN_TEMPLATE_PATHS["ecs_fargate"].read_text())
    assert eks["Resources"]["LaunchpadDeploymentRole"]["Properties"]["MaxSessionDuration"] == 7200
    assert "MaxSessionDuration" not in ecs_fargate["Resources"]["LaunchpadDeploymentRole"]["Properties"]


def test_cfn_parameters_require_external_ids_as_a_list_with_no_default():
    for path in generate.CFN_TEMPLATE_PATHS.values():
        template = json.loads(path.read_text())
        external_ids = template["Parameters"]["ExternalIds"]
        assert external_ids["Type"] == "CommaDelimitedList"
        assert "Default" not in external_ids
        account_id = template["Parameters"]["LaunchpadPlatformAccountId"]
        assert account_id["AllowedPattern"] == "^[0-9]{12}$"


def test_cfn_role_and_policy_names_are_fixed_matching_authenticate_py():
    for path in generate.CFN_TEMPLATE_PATHS.values():
        template = json.loads(path.read_text())
        role = template["Resources"]["LaunchpadDeploymentRole"]["Properties"]
        policy = template["Resources"]["LaunchpadDeploymentPolicy"]["Properties"]
        assert role["RoleName"] == "LaunchpadDeploymentRole"
        assert policy["ManagedPolicyName"] == "LaunchpadDeploymentPolicy"
        assert policy["Roles"] == [{"Ref": "LaunchpadDeploymentRole"}]


def test_bumping_over_a_changed_eks_only_statement_leaves_ecs_fargate_alone(sandbox):
    """Mirrors the bug this design fixes: a compute-type-only change must not raise the
    required version for compute types whose rendered document didn't change."""
    next_version = policy_data.version() + 1
    baseline_ecs_fargate_required = policy_data.required_version_for("ecs_fargate")
    data = json.loads(sandbox.policy.read_text())
    data["compute_type_statements"]["eks"][0]["Action"].append("eks:TagResource")
    _rewrite_policy(
        sandbox.policy,
        compute_type_statements=data["compute_type_statements"],
        version=next_version,
    )

    assert generate.run(write=True) == 0

    assert policy_data.required_version_for("ecs_fargate") == baseline_ecs_fargate_required
    assert policy_data.required_version_for("eks") == next_version
