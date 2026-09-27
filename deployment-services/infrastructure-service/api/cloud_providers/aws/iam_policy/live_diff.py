"""Diff a customer's actual, live IAM state against what `policy_data` says Launchpad
should have. This is the preflight the onboarding flow wants: a customer whose applied
policy is behind gets an actionable diff instead of a terraform stack trace deep in a
provisioning run.

Kept in this package because it belongs next to the source of truth it diffs against,
but deliberately NOT re-exported from `iam_policy/__init__.py` and NOT imported by
`generate.py` — unlike `policy_data.py`, this module takes a live boto3 IAM client and
has no reason to run in the CI `--check` path.

`infra` is duck-typed (`.id`, `.code`, `.compute_type`) rather than the Django
`Infrastructure` model, so this module stays free of a Django import — matching the
same constraint `policy_data.py` documents for itself.
"""

import json
from dataclasses import dataclass, field
from urllib.parse import unquote

from botocore.exceptions import ClientError

from . import policy_data

ROLE_NAME = "LaunchpadDeploymentRole"
POLICY_NAME = "LaunchpadDeploymentPolicy"


def _decode_policy_document(document) -> dict:
    """IAM policy documents come back URL-encoded JSON strings on the wire; botocore's
    `json_decode_policies` handler normally decodes them into a dict before boto3 ever
    sees them, but a Stubber-fed response can arrive as the raw string it validated
    against the service model. Handle either shape."""
    if isinstance(document, str):
        return json.loads(unquote(document))
    return document


def _as_list(value) -> list:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _expand_statement_grants(statement: dict) -> dict[tuple, str]:
    """One grant row per action in a statement, each carrying its own scope so a
    single-action difference inside a multi-action statement is reported as one missing
    or extra grant, not the whole statement. `Sid` is not part of the grant — it's a
    label, not a permission.

    Returns a map from a case-normalized identity tuple (IAM action names are
    case-insensitive, so identity must be too) to that action's original spelling, so a
    rendered diff shows the auditor the casing AWS actually returned rather than a
    lowercased reconstruction."""
    action_key = "Action" if "Action" in statement else "NotAction"
    resource_key = "Resource" if "Resource" in statement else "NotResource"
    effect = statement.get("Effect")
    actions = _as_list(statement.get(action_key))
    resources = tuple(sorted(_as_list(statement.get(resource_key))))
    condition = json.dumps(statement.get("Condition"), sort_keys=True) if statement.get("Condition") else None

    return {
        (effect, action_key, action.lower(), resource_key, resources, condition): action
        for action in actions
    }


def _grant_row_to_dict(row: tuple, action_display: str) -> dict:
    effect, action_key, _action_lower, resource_key, resources, condition = row
    result = {"effect": effect, action_key.lower(): action_display, resource_key.lower(): list(resources)}
    if condition:
        result["condition"] = json.loads(condition)
    return result


@dataclass
class PolicyDiff:
    available: bool
    reason: str | None = None
    missing_grants: list[dict] = field(default_factory=list)
    extra_grants: list[dict] = field(default_factory=list)
    identical: bool = False

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "missing_grants": self.missing_grants,
            "extra_grants": self.extra_grants,
            "identical": self.identical,
        }


@dataclass
class TrustPolicyDiff:
    available: bool
    reason: str | None = None
    principal_matches: bool = False
    external_id_present: bool = False
    external_id_matches: bool = False
    identical: bool = False

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "principal_matches": self.principal_matches,
            "external_id_present": self.external_id_present,
            "external_id_matches": self.external_id_matches,
            "identical": self.identical,
        }


@dataclass
class LiveDriftReport:
    policy: PolicyDiff
    trust_policy: TrustPolicyDiff

    def to_dict(self) -> dict:
        return {"policy": self.policy.to_dict(), "trust_policy": self.trust_policy.to_dict()}


def _client_error_reason(exc: ClientError, operation: str) -> str:
    # Only the AWS error code, never Error.Message: it carries the assumed-role ARN
    # (the same class of leak fixed for provisioning logs and terraform stderr).
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    return f"{operation} failed: {code}"


def _diff_managed_policy(iam, infra) -> PolicyDiff:
    try:
        attached = iam.list_attached_role_policies(RoleName=ROLE_NAME)["AttachedPolicies"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return PolicyDiff(available=False, reason="role_missing")
        return PolicyDiff(available=False, reason=_client_error_reason(e, "ListAttachedRolePolicies"))

    match = next((p for p in attached if p.get("PolicyName") == POLICY_NAME), None)
    if match is None:
        return PolicyDiff(available=False, reason="policy_not_attached")

    try:
        policy = iam.get_policy(PolicyArn=match["PolicyArn"])["Policy"]
        version = iam.get_policy_version(
            PolicyArn=match["PolicyArn"], VersionId=policy["DefaultVersionId"]
        )["PolicyVersion"]
    except ClientError as e:
        return PolicyDiff(available=False, reason=_client_error_reason(e, "GetPolicyVersion"))

    live_doc = _decode_policy_document(version["Document"])
    expected_json = policy_data.document_json(infra.compute_type).replace(
        policy_data.ACCOUNT_ID_PLACEHOLDER, str(infra.code)
    )
    expected_doc = json.loads(expected_json)

    expected_grants: dict[tuple, str] = {}
    for statement in expected_doc.get("Statement", []):
        expected_grants.update(_expand_statement_grants(statement))
    live_grants: dict[tuple, str] = {}
    for statement in live_doc.get("Statement", []):
        live_grants.update(_expand_statement_grants(statement))

    missing_keys = sorted(set(expected_grants) - set(live_grants), key=str)
    extra_keys = sorted(set(live_grants) - set(expected_grants), key=str)
    return PolicyDiff(
        available=True,
        missing_grants=[_grant_row_to_dict(k, expected_grants[k]) for k in missing_keys],
        extra_grants=[_grant_row_to_dict(k, live_grants[k]) for k in extra_keys],
        identical=not missing_keys and not extra_keys,
    )


def _diff_trust_policy(iam, infra, platform_principal_arn: str) -> TrustPolicyDiff:
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return TrustPolicyDiff(available=False, reason="role_missing")
        return TrustPolicyDiff(available=False, reason=_client_error_reason(e, "GetRole"))

    trust_doc = _decode_policy_document(role["AssumeRolePolicyDocument"])
    statements = trust_doc.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]

    principal_matches = False
    external_id_present = False
    external_id_matches = False
    for statement in statements:
        if statement.get("Effect") != "Allow":
            continue
        if "sts:assumerole" not in [a.lower() for a in _as_list(statement.get("Action"))]:
            continue
        principal = statement.get("Principal", {})
        principal_aws = _as_list(principal.get("AWS")) if isinstance(principal, dict) else []
        if platform_principal_arn in principal_aws:
            principal_matches = True

        condition = statement.get("Condition") or {}
        external_id = (condition.get("StringEquals") or {}).get("sts:ExternalId")
        if external_id is not None:
            external_id_present = True
            if external_id == str(infra.id):
                external_id_matches = True

    return TrustPolicyDiff(
        available=True,
        principal_matches=principal_matches,
        external_id_present=external_id_present,
        external_id_matches=external_id_matches,
        identical=principal_matches and external_id_present and external_id_matches,
    )


def diff_live_policy(iam, infra, *, platform_principal_arn: str) -> LiveDriftReport:
    """Diff `infra`'s live IAM state (managed policy + trust policy) against what
    `policy_data` says it should be.

    `iam` is a boto3 IAM client already authenticated as the assumed role in the
    customer's account (see `api.services.evidence_pack._iam_client_for`) — this module
    never calls `sts:AssumeRole` itself. The two halves are diffed independently: a role
    that exists but has no attached policy still gets a trust-policy verdict, and vice
    versa.
    """
    return LiveDriftReport(
        policy=_diff_managed_policy(iam, infra),
        trust_policy=_diff_trust_policy(iam, infra, platform_principal_arn),
    )
