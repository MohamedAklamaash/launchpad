"""diff_live_policy against a Stubber-fed IAM client: identical policy/trust policy,
a live policy missing a grant, one with an extra grant, action-list-order insensitivity,
and every documented error surface (role missing, policy not attached, AccessDenied)."""
import copy
import json

import boto3
import pytest
from api.cloud_providers.aws.iam_policy import policy_data
from api.cloud_providers.aws.iam_policy.live_diff import diff_live_policy
from botocore.stub import ANY, Stubber

PLATFORM_PRINCIPAL_ARN = "arn:aws:iam::221082203366:user/aklamaash-terraform"
ACCOUNT_ID = "123456789012"


class FakeInfra:
    def __init__(self, *, id="infra-uuid-1", code=ACCOUNT_ID, compute_type="ecs_fargate"):
        self.id = id
        self.code = code
        self.compute_type = compute_type


@pytest.fixture
def iam_client():
    return boto3.client("iam", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")


def _attached_policies_response():
    return {
        "AttachedPolicies": [
            {"PolicyName": "LaunchpadDeploymentPolicy", "PolicyArn": f"arn:aws:iam::{ACCOUNT_ID}:policy/LaunchpadDeploymentPolicy"}
        ]
    }


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


def _stub_full(stubber, *, policy_doc, trust_doc):
    stubber.add_response("list_attached_role_policies", _attached_policies_response(), {"RoleName": ANY})
    stubber.add_response("get_policy", _get_policy_response(), {"PolicyArn": ANY})
    stubber.add_response("get_policy_version", _get_policy_version_response(policy_doc), {"PolicyArn": ANY, "VersionId": ANY})
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
    assert report.policy.missing_grants == []
    assert report.policy.extra_grants == []
    assert report.trust_policy.available is True
    assert report.trust_policy.identical is True
    assert report.trust_policy.principal_matches is True
    assert report.trust_policy.external_id_matches is True


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

def test_live_policy_missing_a_grant_is_reported(iam_client):
    infra = FakeInfra()
    policy_doc = copy.deepcopy(policy_data.document(infra.compute_type))
    removed = policy_doc["Statement"][0]["Action"].pop()

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert report.policy.extra_grants == []
    assert len(report.policy.missing_grants) == 1
    assert report.policy.missing_grants[0]["action"] == removed


def test_live_policy_with_an_extra_grant_is_reported(iam_client):
    infra = FakeInfra()
    policy_doc = copy.deepcopy(policy_data.document(infra.compute_type))
    policy_doc["Statement"].append({"Effect": "Allow", "Action": "sns:Publish", "Resource": "*"})

    with Stubber(iam_client) as stubber:
        _stub_full(stubber, policy_doc=policy_doc, trust_doc=_expected_trust_doc(infra))
        report = diff_live_policy(iam_client, infra, platform_principal_arn=PLATFORM_PRINCIPAL_ARN)

    assert report.policy.identical is False
    assert report.policy.missing_grants == []
    assert len(report.policy.extra_grants) == 1
    assert report.policy.extra_grants[0]["action"] == "sns:Publish"


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
