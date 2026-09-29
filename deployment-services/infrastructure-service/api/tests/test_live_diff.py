"""live_diff's trust-policy comparison for the multi-infra case (F5): more than one
Launchpad infrastructure can share an AWS account's LaunchpadDeploymentRole, in which
case sts:ExternalId on the wire is a list rather than a bare string. This must not be
misread as drift — other ids in the list are legitimate siblings, reported as a count."""
from types import SimpleNamespace

from api.cloud_providers.aws.iam_policy.live_diff import _diff_trust_policy

PLATFORM_PRINCIPAL_ARN = "arn:aws:iam::221082203366:user/aklamaash-terraform"


def _infra(infra_id):
    return SimpleNamespace(id=infra_id, code="123456789012", compute_type="ecs_fargate")


def _statement(external_id):
    return {
        "Effect": "Allow",
        "Principal": {"AWS": PLATFORM_PRINCIPAL_ARN},
        "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"sts:ExternalId": external_id}},
    }


class _FakeIam:
    def __init__(self, statements):
        self._document = {"Version": "2012-10-17", "Statement": statements}

    def get_role(self, RoleName):  # matches boto3's parameter casing
        return {"Role": {"AssumeRolePolicyDocument": self._document}}


def test_single_string_external_id_is_identical_with_no_siblings():
    infra = _infra("infra-a")
    iam = _FakeIam([_statement("infra-a")])

    diff = _diff_trust_policy(iam, infra, PLATFORM_PRINCIPAL_ARN)

    assert diff.identical is True
    assert diff.external_id_matches is True
    assert diff.sibling_external_ids == 0
    assert diff.extra_statements == []


def test_list_external_id_with_this_infra_and_a_sibling_is_identical():
    infra = _infra("infra-a")
    iam = _FakeIam([_statement(["infra-a", "infra-b"])])

    diff = _diff_trust_policy(iam, infra, PLATFORM_PRINCIPAL_ARN)

    assert diff.identical is True
    assert diff.external_id_present is True
    assert diff.external_id_matches is True
    assert diff.sibling_external_ids == 1
    assert diff.extra_statements == []


def test_list_external_id_with_several_siblings_counts_all_of_them():
    infra = _infra("infra-a")
    iam = _FakeIam([_statement(["infra-a", "infra-b", "infra-c"])])

    diff = _diff_trust_policy(iam, infra, PLATFORM_PRINCIPAL_ARN)

    assert diff.identical is True
    assert diff.sibling_external_ids == 2


def test_list_external_id_missing_this_infra_does_not_match():
    infra = _infra("infra-a")
    iam = _FakeIam([_statement(["infra-b", "infra-c"])])

    diff = _diff_trust_policy(iam, infra, PLATFORM_PRINCIPAL_ARN)

    assert diff.identical is False
    assert diff.external_id_matches is False


def test_role_missing_is_reported_as_unavailable():
    infra = _infra("infra-a")

    class _MissingRoleIam:
        def get_role(self, RoleName):
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "NoSuchEntity"}}, "GetRole")

    diff = _diff_trust_policy(_MissingRoleIam(), infra, PLATFORM_PRINCIPAL_ARN)

    assert diff.available is False
    assert diff.reason == "role_missing"
