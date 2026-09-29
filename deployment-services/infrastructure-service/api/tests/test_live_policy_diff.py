"""diff_live_policy against a Stubber-fed IAM client: identical policy/trust policy,
a live policy missing/gaining an Allow or a Deny, an Allow+NotAction statement reported
as a whole-statement `grants_all_except` row, action-list-order and case insensitivity,
a second attached/inline policy invisible to the row diff, a trust policy that trusts an
extra principal or splits the right principal and the right ExternalId across two
statements, and every documented error surface (role missing, policy not attached,
AccessDenied)."""
import copy
import json

import boto3
import pytest
from api.cloud_providers.aws.iam_policy import policy_data
from api.cloud_providers.aws.iam_policy.live_diff import diff_live_policy
from botocore.stub import ANY, Stubber

PLATFORM_PRINCIPAL_ARN = "arn:aws:iam::210987654321:user/launchpad-platform"
ACCOUNT_ID = "123456789012"


class FakeInfra:
    def __init__(self, *, id="infra-uuid-1", code=ACCOUNT_ID, compute_type="ecs_fargate"):
        self.id = id
        self.code = code
        self.compute_type = compute_type


@pytest.fixture
def iam_client():
    return boto3.client("iam", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")


def _attached_policies_response(extra_policies=()):
    return {
        "AttachedPolicies": [
            {"PolicyName": "LaunchpadDeploymentPolicy", "PolicyArn": f"arn:aws:iam::{ACCOUNT_ID}:policy/LaunchpadDeploymentPolicy"},
            *extra_policies,
        ]
    }


def _inline_policies_response(names=()):
    return {"PolicyNames": list(names)}


def _get_policy_response():
    return {
        "Policy": {
            "PolicyName": "LaunchpadDeploymentPolicy",
            "Arn": f"arn:aws:iam::{ACCOUNT_ID}:policy/LaunchpadDeploymentPolicy",
            "DefaultVersionId": "v1",
        }
    }


def _get_policy_version_response(document: dict):
    return {"PolicyVersion": {"Document": json.dumps(document), "VersionId": "v1", "IsDefaultVersion": True}}


def _role_response(document: dict):
    return {
        "Role": {
            "Path": "/",
            "RoleName": "LaunchpadDeploymentRole",
            "RoleId": "AROA000000000000EXAMP",
            "Arn": f"arn:aws:iam::{ACCOUNT_ID}:role/LaunchpadDeploymentRole",
            "CreateDate": "2024-01-01T00:00:00Z",
            "AssumeRolePolicyDocument": json.dumps(document),
        }
    }


def _expected_trust_doc(infra):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": PLATFORM_PRINCIPAL_ARN},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"sts:ExternalId": str(infra.id)}},
            }
        ],
    }


def _role_tags_response(compute_types_tag_value=None):
    tags = [{"Key": "launchpad:compute-types", "Value": compute_types_tag_value}] if compute_types_tag_value else []
    return {"Tags": tags, "IsTruncated": False}


def _stub_full(stubber, *, policy_doc, trust_doc, extra_attached=(), inline_names=(), compute_types_tag=None):
    stubber.add_response("list_attached_role_policies", _attached_policies_response(extra_attached), {"RoleName": ANY})
    stubber.add_response("list_role_policies", _inline_policies_response(inline_names), {"RoleName": ANY})
    stubber.add_response("get_policy", _get_policy_response(), {"PolicyArn": ANY})
    stubber.add_response("get_policy_version", _get_policy_version_response(policy_doc), {"PolicyArn": ANY, "VersionId": ANY})
    stubber.add_response("list_role_tags", _role_tags_response(compute_types_tag), {"RoleName": ANY})
    stubber.add_response("get_role", _role_response(trust_doc), {"RoleName": ANY})


# ── identical policy + trust policy ──────────────────────────────────────────────

def test_identical_policy_and_trust_policy_report_no_drift(iam_client):
    infra = FakeInfra()
    policy_doc = policy_data.document(infra.compute_type)
    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.available is True
    assert report.policy.identical is True
    assert report.policy.missing_allows == []
    assert report.policy.missing_denies == []
    assert report.policy.extra_allows == []
    assert report.policy.extra_denies == []
    assert report.policy.other_policies is None
    assert report.trust_policy.available is True
    assert report.trust_policy.identical is True
    assert report.trust_policy.principal_matches is True
    assert report.trust_policy.external_id_matches is True
    assert report.trust_policy.extra_statements == []


def test_order_insensitive_action_list_and_statement_order(iam_client):
    """Shuffling an Action list and the statement order must not read as drift —
    IAM policy semantics don't depend on either."""
    infra = FakeInfra()
    policy_doc = copy.deepcopy(policy_data.document(infra.compute_type))
    first = policy_doc["Statement"][0]
    assert isinstance(first["Action"], list) and len(first["Action"]) > 1
    first["Action"] = list(reversed(first["Action"]))
    policy_doc["Statement"] = list(reversed(policy_doc["Statement"]))

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is True


# ── managed policy drift ─────────────────────────────────────────────────────────

def test_live_policy_missing_an_allow_is_reported(iam_client):
    infra = FakeInfra()
    policy_doc = copy.deepcopy(policy_data.document(infra.compute_type))
    removed = policy_doc["Statement"][0]["Action"].pop()

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert report.policy.extra_allows == []
    assert len(report.policy.missing_allows) == 1
    assert report.policy.missing_allows[0]["action"] == removed


def test_live_policy_with_an_extra_allow_is_reported(iam_client):
    infra = FakeInfra()
    policy_doc = copy.deepcopy(policy_data.document(infra.compute_type))
    policy_doc["Statement"].append({"Effect": "Allow", "Action": "sns:Publish", "Resource": "*"})

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert report.policy.missing_allows == []
    assert len(report.policy.extra_allows) == 1
    assert report.policy.extra_allows[0]["action"] == "sns:Publish"


def test_removed_deny_statement_is_a_missing_deny_not_a_missing_allow(iam_client):
    """A customer-side edit that deletes an expected Deny (e.g. the EKS access-entry
    backstop) must be classified as a widening risk, distinct from a missing Allow."""
    infra = FakeInfra(compute_type="eks")
    expected_json = policy_data.document_json("eks").replace(policy_data.ACCOUNT_ID_PLACEHOLDER, infra.code)
    expected_doc = json.loads(expected_json)
    deny_statements = [s for s in expected_doc["Statement"] if s.get("Effect") == "Deny"]
    assert len(deny_statements) == 1
    live_doc = copy.deepcopy(expected_doc)
    live_doc["Statement"] = [s for s in live_doc["Statement"] if s.get("Effect") != "Deny"]

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=live_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert report.policy.missing_allows == []
    assert len(report.policy.missing_denies) == 1
    assert report.policy.missing_denies[0]["effect"] == "Deny"
    assert report.policy.missing_denies[0]["grants_all_except"] is True


def test_allow_with_notaction_is_one_whole_statement_row(iam_client):
    """Allow + NotAction grants an unbounded set of actions — it must not be exploded
    per-action (which would understate it as a handful of narrow grants) and must not be
    silently invisible either."""
    infra = FakeInfra()
    policy_doc = copy.deepcopy(policy_data.document(infra.compute_type))
    policy_doc["Statement"].append({"Effect": "Allow", "NotAction": "s3:*", "Resource": "*"})

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert len(report.policy.extra_allows) == 1
    row = report.policy.extra_allows[0]
    assert row["grants_all_except"] is True
    assert row["notaction"] == ["s3:*"]


def test_action_case_is_ignored_for_identity_but_preserved_for_display(iam_client):
    """IAM action names are case-insensitive; a live policy that differs from the
    expected one only by casing must not read as drift, and a genuine extra grant must
    still be reported in the casing AWS actually returned, not lowercased."""
    infra = FakeInfra()
    policy_doc = copy.deepcopy(policy_data.document(infra.compute_type))
    policy_doc["Statement"][0]["Action"] = [a.upper() for a in policy_doc["Statement"][0]["Action"]]

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is True


def test_eks_compute_type_substitutes_account_id_before_diffing(iam_client):
    infra = FakeInfra(compute_type="eks")
    expected_json = policy_data.document_json("eks").replace(policy_data.ACCOUNT_ID_PLACEHOLDER, infra.code)
    policy_doc = json.loads(expected_json)

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is True


# ── shared role serving several compute_types (F5 evidence pack bug) ───────────────

def test_ecs_infra_on_role_shared_with_eks_sibling_expects_the_eks_document(iam_client):
    """An ecs_fargate infra whose role also serves an eks sibling has the eks (superset)
    document installed on it — create_aws_role.sh never downgrades. The live diff must
    expect that same superset, not just the ecs_fargate document, or every EKS-only
    grant reads as unexplained drift."""
    infra = FakeInfra(compute_type="ecs_fargate")
    eks_doc = json.loads(policy_data.document_json("eks").replace(policy_data.ACCOUNT_ID_PLACEHOLDER, infra.code))

    with Stubber(iam_client) as stubber:
        _stub_full(
            stubber,
            policy_doc=eks_doc,
            trust_doc=_expected_trust_doc(infra),
            compute_types_tag="ecs_fargate+eks",
        )
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is True
    assert report.policy.missing_allows == []
    assert report.policy.extra_allows == []
    assert report.policy.expected_compute_types == ["ecs_fargate", "eks"]


def test_ecs_infra_on_shared_role_with_a_genuine_extra_grant_is_still_drift(iam_client):
    """The union widens what's expected, but it doesn't launder a grant beyond that
    union — something genuinely extra must still surface."""
    infra = FakeInfra(compute_type="ecs_fargate")
    live_doc = json.loads(policy_data.document_json("eks").replace(policy_data.ACCOUNT_ID_PLACEHOLDER, infra.code))
    live_doc["Statement"].append({"Effect": "Allow", "Action": "sns:Publish", "Resource": "*"})

    with Stubber(iam_client) as stubber:
        _stub_full(
            stubber,
            policy_doc=live_doc,
            trust_doc=_expected_trust_doc(infra),
            compute_types_tag="ecs_fargate+eks",
        )
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert len(report.policy.extra_allows) == 1
    assert report.policy.extra_allows[0]["action"] == "sns:Publish"
    assert report.policy.expected_compute_types == ["ecs_fargate", "eks"]


def test_no_compute_types_tag_falls_back_to_this_infras_own_type(iam_client):
    """No tag at all (a role created before the tag existed, or a single-infra role
    never tagged) keeps the pre-union behaviour: the eks grants on a shared role with no
    tag still read as extra, exactly as before this fix."""
    infra = FakeInfra(compute_type="ecs_fargate")
    eks_doc = json.loads(policy_data.document_json("eks").replace(policy_data.ACCOUNT_ID_PLACEHOLDER, infra.code))

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=eks_doc, trust_doc=_expected_trust_doc(infra), compute_types_tag=None)
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert len(report.policy.extra_allows) > 0
    assert report.policy.expected_compute_types == ["ecs_fargate"]


def test_garbage_compute_types_tag_value_is_ignored(iam_client):
    """A tag value naming no compute_type policy_data actually knows falls back to this
    infra's own type, same as a missing tag — never crashes and never silently trusts an
    unrecognised name."""
    infra = FakeInfra(compute_type="ecs_fargate")
    eks_doc = json.loads(policy_data.document_json("eks").replace(policy_data.ACCOUNT_ID_PLACEHOLDER, infra.code))

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=eks_doc, trust_doc=_expected_trust_doc(infra), compute_types_tag="garbage")
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert len(report.policy.extra_allows) > 0
    assert report.policy.expected_compute_types == ["ecs_fargate"]


# ── other attached/inline policies (R1) ────────────────────────────────────────────

def test_administrator_access_attachment_is_reported_and_breaks_identical(iam_client):
    infra = FakeInfra()
    with Stubber(iam_client) as stubber:
        _stub_full(
            stubber,
            policy_doc=policy_data.document(infra.compute_type),
            trust_doc=_expected_trust_doc(infra),
            extra_attached=[{"PolicyName": "AdministratorAccess", "PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess"}],
        )
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert report.policy.other_policies is not None
    assert "arn:aws:iam::aws:policy/AdministratorAccess" in report.policy.other_policies["attached"]


def test_inline_policy_is_reported_and_breaks_identical(iam_client):
    infra = FakeInfra()
    with Stubber(iam_client) as stubber:
        _stub_full(
            stubber,
            policy_doc=policy_data.document(infra.compute_type),
            trust_doc=_expected_trust_doc(infra),
            inline_names=["backdoor-inline-policy"],
        )
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert report.policy.other_policies is not None
    assert "backdoor-inline-policy" in report.policy.other_policies["inline"]


# ── trust policy drift ────────────────────────────────────────────────────────────

def test_trust_policy_wrong_external_id_is_flagged(iam_client):
    infra = FakeInfra()
    trust_doc = _expected_trust_doc(infra)
    trust_doc["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] = "some-other-id"

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_data.document(infra.compute_type), trust_doc=trust_doc)
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.trust_policy.available is True
    assert report.trust_policy.external_id_present is True
    assert report.trust_policy.external_id_matches is False
    assert report.trust_policy.identical is False


def test_trust_policy_missing_external_id_condition_is_flagged(iam_client):
    infra = FakeInfra()
    trust_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Principal": {"AWS": PLATFORM_PRINCIPAL_ARN}, "Action": "sts:AssumeRole"}
        ],
    }

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_data.document(infra.compute_type), trust_doc=trust_doc)
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.trust_policy.external_id_present is False
    assert report.trust_policy.external_id_matches is False
    assert report.trust_policy.identical is False


def test_trust_policy_wrong_principal_is_flagged(iam_client):
    infra = FakeInfra()
    trust_doc = _expected_trust_doc(infra)
    trust_doc["Statement"][0]["Principal"]["AWS"] = "arn:aws:iam::999999999999:user/someone-else"

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_data.document(infra.compute_type), trust_doc=trust_doc)
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.trust_policy.principal_matches is False
    assert report.trust_policy.identical is False


def test_extra_assume_role_statement_is_flagged_and_breaks_identical(iam_client):
    """B1: the correct, narrow statement is present, but a second, wider statement also
    grants sts:AssumeRole — to anyone. Must not report identical."""
    infra = FakeInfra()
    trust_doc = _expected_trust_doc(infra)
    trust_doc["Statement"].append({"Effect": "Allow", "Principal": "*", "Action": "sts:*"})

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_data.document(infra.compute_type), trust_doc=trust_doc)
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.trust_policy.principal_matches is True
    assert report.trust_policy.external_id_matches is True
    assert len(report.trust_policy.extra_statements) == 1
    assert report.trust_policy.extra_statements[0]["Principal"] == "*"
    assert report.trust_policy.identical is False


def test_federated_assume_role_with_web_identity_is_flagged_as_extra(iam_client):
    """AssumeRoleWithWebIdentity/AssumeRoleWithSAML are a second, principal-different way
    to obtain this role's credentials (e.g. GitHub OIDC) — a check narrowly scoped to the
    literal sts:AssumeRole action alone must not treat this as irrelevant."""
    infra = FakeInfra()
    trust_doc = _expected_trust_doc(infra)
    trust_doc["Statement"].append({
        "Effect": "Allow",
        "Principal": {"Federated": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"},
        "Action": "sts:AssumeRoleWithWebIdentity",
    })

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_data.document(infra.compute_type), trust_doc=trust_doc)
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert len(report.trust_policy.extra_statements) == 1
    assert report.trust_policy.extra_statements[0]["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert report.trust_policy.identical is False


def test_split_principal_and_external_id_across_two_statements_does_not_report_identical(iam_client):
    """B1's exact bug: statement A has the right principal but no condition; statement B
    has a different principal but the right ExternalId. The old implementation set
    principal_matches from A and external_id_matches from B independently, reporting a
    trust policy that in fact lets a second, uncontrolled principal assume the role."""
    infra = FakeInfra()
    statement_a = {"Effect": "Allow", "Principal": {"AWS": PLATFORM_PRINCIPAL_ARN}, "Action": "sts:AssumeRole"}
    statement_b = {
        "Effect": "Allow",
        "Principal": {"AWS": "arn:aws:iam::999999999999:user/someone-else"},
        "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"sts:ExternalId": str(infra.id)}},
    }
    trust_doc = {"Version": "2012-10-17", "Statement": [statement_a, statement_b]}

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_data.document(infra.compute_type), trust_doc=trust_doc)
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.trust_policy.identical is False
    assert len(report.trust_policy.extra_statements) == 1
    assert report.trust_policy.extra_statements[0]["Principal"]["AWS"] == "arn:aws:iam::999999999999:user/someone-else"


# ── error surfaces ────────────────────────────────────────────────────────────────

def test_role_missing_is_reported_on_both_halves(iam_client):
    infra = FakeInfra()
    with Stubber(iam_client) as stubber:
        stubber.add_client_error("list_attached_role_policies", service_error_code="NoSuchEntity")
        stubber.add_client_error("get_role", service_error_code="NoSuchEntity")
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.available is False
    assert report.policy.reason == "role_missing"
    assert report.trust_policy.available is False
    assert report.trust_policy.reason == "role_missing"


def test_policy_not_attached_is_reported(iam_client):
    infra = FakeInfra()
    with Stubber(iam_client) as stubber:
        stubber.add_response("list_attached_role_policies", {"AttachedPolicies": []}, {"RoleName": ANY})
        stubber.add_response("list_role_policies", {"PolicyNames": []}, {"RoleName": ANY})
        stubber.add_response("get_role", _role_response(_expected_trust_doc(infra)), {"RoleName": ANY})
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.available is False
    assert report.policy.reason == "policy_not_attached"
    assert report.trust_policy.available is True


def test_access_denied_is_reported_without_leaking_the_message(iam_client):
    infra = FakeInfra()
    with Stubber(iam_client) as stubber:
        stubber.add_client_error(
            "list_attached_role_policies",
            service_error_code="AccessDenied",
            service_message=f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/LaunchpadDeploymentRole/launchpad-secret-session is not authorized",
        )
        stubber.add_client_error("get_role", service_error_code="AccessDenied", service_message="also secret")
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.available is False
    assert "AccessDenied" in report.policy.reason
    assert "secret" not in report.policy.reason
    assert report.trust_policy.available is False
    assert "AccessDenied" in report.trust_policy.reason
