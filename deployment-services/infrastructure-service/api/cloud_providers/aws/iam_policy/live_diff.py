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

import fnmatch
import json
from dataclasses import dataclass, field
from urllib.parse import unquote

from botocore.exceptions import ClientError

from . import policy_data

ROLE_NAME = "LaunchpadDeploymentRole"
POLICY_NAME = "LaunchpadDeploymentPolicy"

_ASSUME_ROLE_ACTION = "sts:assumerole"


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


def _statements_of(document: dict) -> list[dict]:
    """A policy document's `Statement` is a single object when there's exactly one
    statement — normalise to a list either way, so a single-statement document doesn't
    raise or get silently mishandled downstream."""
    statements = document.get("Statement", [])
    return [statements] if isinstance(statements, dict) else list(statements)


def _wildcard_match(pattern: str, value: str) -> bool:
    """IAM action wildcards (`*`, `?`) matched case-insensitively, e.g. "sts:*" or
    "sts:Assume*" both match "sts:AssumeRole"."""
    return fnmatch.fnmatchcase(value.lower(), pattern.lower())


def _grants_role_assumption(action: str) -> bool:
    """True if `action` could let some principal obtain this role's credentials: either
    a wildcard that happens to cover the literal `sts:AssumeRole` (`sts:*`, `*`,
    `sts:Assume*`), or one of the federated assume-role actions
    (`sts:AssumeRoleWithSAML`, `sts:AssumeRoleWithWebIdentity`) — a second, principal-
    different way into the role that a check narrowly scoped to `sts:AssumeRole` alone
    would miss entirely."""
    lowered = action.lower()
    return _wildcard_match(action, _ASSUME_ROLE_ACTION) or fnmatch.fnmatchcase(lowered, "sts:assumerole*")


def _is_open_ended(statement: dict) -> bool:
    return "NotAction" in statement or "NotResource" in statement


def _open_ended_row(statement: dict) -> tuple[tuple, dict]:
    """A NotAction/NotResource statement grants (or denies) an unbounded set of actions
    — exploding it per-action the way a normal Action/Resource statement is exploded
    would understate its breadth instead of flagging it. Reported as a single row."""
    effect = statement.get("Effect")
    action_key = "Action" if "Action" in statement else "NotAction"
    resource_key = "Resource" if "Resource" in statement else "NotResource"
    action_values = sorted(_as_list(statement.get(action_key)))
    resource_values = sorted(_as_list(statement.get(resource_key)))
    condition = statement.get("Condition")
    condition_json = json.dumps(condition, sort_keys=True) if condition else None

    key = (
        "open_ended",
        effect,
        action_key,
        tuple(a.lower() for a in action_values),
        resource_key,
        tuple(resource_values),
        condition_json,
    )
    row = {
        "effect": effect,
        "grants_all_except": True,
        action_key.lower(): action_values,
        resource_key.lower(): resource_values,
    }
    if condition:
        row["condition"] = condition
    return key, row


def _expand_statement_grants(statement: dict) -> dict[tuple, dict]:
    """One row per action in a normal Action/Resource statement (each carrying its own
    scope, so a single-action difference inside a multi-action statement is reported as
    one missing/extra grant, not the whole statement), or a single whole-statement row
    for a NotAction/NotResource statement (see `_open_ended_row`). `Sid` is never part
    of the identity — it's a label, not a permission.

    Row identity is case-insensitive on the action (IAM action names are), but the
    returned row keeps the original spelling for display."""
    if _is_open_ended(statement):
        key, row = _open_ended_row(statement)
        return {key: row}

    action_key = "Action" if "Action" in statement else "NotAction"
    resource_key = "Resource" if "Resource" in statement else "NotResource"
    effect = statement.get("Effect")
    actions = _as_list(statement.get(action_key))
    resources = tuple(sorted(_as_list(statement.get(resource_key))))
    condition = statement.get("Condition")
    condition_json = json.dumps(condition, sort_keys=True) if condition else None

    rows = {}
    for action in actions:
        key = ("action", effect, action_key, action.lower(), resource_key, resources, condition_json)
        row = {"effect": effect, action_key.lower(): action, resource_key.lower(): list(resources)}
        if condition:
            row["condition"] = condition
        rows[key] = row
    return rows


@dataclass
class PolicyDiff:
    available: bool
    reason: str | None = None
    missing_allows: list[dict] = field(default_factory=list)
    missing_denies: list[dict] = field(default_factory=list)
    extra_allows: list[dict] = field(default_factory=list)
    extra_denies: list[dict] = field(default_factory=list)
    other_policies: dict | None = None
    identical: bool = False

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "missing_allows": self.missing_allows,
            "missing_denies": self.missing_denies,
            "extra_allows": self.extra_allows,
            "extra_denies": self.extra_denies,
            "other_policies": self.other_policies,
            "identical": self.identical,
        }


@dataclass
class TrustPolicyDiff:
    available: bool
    reason: str | None = None
    principal_matches: bool = False
    external_id_present: bool = False
    external_id_matches: bool = False
    extra_statements: list[dict] = field(default_factory=list)
    identical: bool = False

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "principal_matches": self.principal_matches,
            "external_id_present": self.external_id_present,
            "external_id_matches": self.external_id_matches,
            "extra_statements": self.extra_statements,
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


def _other_policies(iam, attached: list[dict]) -> dict | None:
    """Any policy attached to the role other than LaunchpadDeploymentPolicy, plus any
    inline policy — both are invisible to the row-level diff below (it only reads the
    LaunchpadDeploymentPolicy document) but grant real access just the same. An
    AdministratorAccess attachment or an inline policy must never be silently ignored
    just because the one managed policy this module knows to look for matches."""
    other_attached_arns = [p["PolicyArn"] for p in attached if p.get("PolicyName") != POLICY_NAME]
    inline_policy_names = iam.list_role_policies(RoleName=ROLE_NAME)["PolicyNames"]
    if not other_attached_arns and not inline_policy_names:
        return None
    return {"attached": other_attached_arns, "inline": list(inline_policy_names)}


def _diff_managed_policy(iam, infra) -> PolicyDiff:
    try:
        attached = iam.list_attached_role_policies(RoleName=ROLE_NAME)["AttachedPolicies"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return PolicyDiff(available=False, reason="role_missing")
        return PolicyDiff(available=False, reason=_client_error_reason(e, "ListAttachedRolePolicies"))

    try:
        other_policies = _other_policies(iam, attached)
    except ClientError as e:
        return PolicyDiff(available=False, reason=_client_error_reason(e, "ListRolePolicies"))

    match = next((p for p in attached if p.get("PolicyName") == POLICY_NAME), None)
    if match is None:
        return PolicyDiff(available=False, reason="policy_not_attached", other_policies=other_policies)

    try:
        policy = iam.get_policy(PolicyArn=match["PolicyArn"])["Policy"]
        version = iam.get_policy_version(
            PolicyArn=match["PolicyArn"], VersionId=policy["DefaultVersionId"]
        )["PolicyVersion"]
    except ClientError as e:
        return PolicyDiff(available=False, reason=_client_error_reason(e, "GetPolicyVersion"), other_policies=other_policies)

    live_doc = _decode_policy_document(version["Document"])
    expected_json = policy_data.document_json(infra.compute_type).replace(
        policy_data.ACCOUNT_ID_PLACEHOLDER, str(infra.code)
    )
    expected_doc = json.loads(expected_json)

    expected_rows: dict[tuple, dict] = {}
    for statement in _statements_of(expected_doc):
        expected_rows.update(_expand_statement_grants(statement))
    live_rows: dict[tuple, dict] = {}
    for statement in _statements_of(live_doc):
        live_rows.update(_expand_statement_grants(statement))

    missing_keys = sorted(set(expected_rows) - set(live_rows), key=str)
    extra_keys = sorted(set(live_rows) - set(expected_rows), key=str)

    missing_allows = [expected_rows[k] for k in missing_keys if expected_rows[k]["effect"] == "Allow"]
    missing_denies = [expected_rows[k] for k in missing_keys if expected_rows[k]["effect"] == "Deny"]
    extra_allows = [live_rows[k] for k in extra_keys if live_rows[k]["effect"] == "Allow"]
    extra_denies = [live_rows[k] for k in extra_keys if live_rows[k]["effect"] == "Deny"]

    return PolicyDiff(
        available=True,
        missing_allows=missing_allows,
        missing_denies=missing_denies,
        extra_allows=extra_allows,
        extra_denies=extra_denies,
        other_policies=other_policies,
        identical=not missing_keys and not extra_keys and other_policies is None,
    )


def _statement_covers_assume_role(statement: dict) -> bool:
    if statement.get("Effect") != "Allow":
        return False
    if "NotAction" in statement:
        not_actions = _as_list(statement.get("NotAction"))
        return not any(_grants_role_assumption(a) for a in not_actions)
    actions = _as_list(statement.get("Action"))
    return any(_grants_role_assumption(a) for a in actions)


def _is_narrow_assume_role_statement(statement: dict, platform_principal_arn: str) -> bool:
    """The one shape a trust statement must have to be treated as *the* intended grant:
    exactly the literal action `sts:AssumeRole` (no wildcard, no NotAction), trusting
    exactly one principal — the Launchpad platform ARN — and nothing else (no
    NotPrincipal, no extra entries in Principal.AWS, no "*"). Anything wider than this
    is, by definition, an extra statement — see `_diff_trust_policy`."""
    if "NotAction" in statement or "NotPrincipal" in statement:
        return False
    actions = _as_list(statement.get("Action"))
    if len(actions) != 1 or actions[0].lower() != _ASSUME_ROLE_ACTION:
        return False
    principal = statement.get("Principal")
    if not isinstance(principal, dict) or set(principal.keys()) != {"AWS"}:
        return False
    return _as_list(principal.get("AWS")) == [platform_principal_arn]


def _external_id_from_statement(statement: dict) -> str | None:
    condition = statement.get("Condition") or {}
    return (condition.get("StringEquals") or {}).get("sts:ExternalId")


def _summarize_trust_statement(statement: dict) -> dict:
    return {k: v for k, v in statement.items() if k in ("Effect", "Action", "NotAction", "Principal", "NotPrincipal", "Condition")}


def _diff_trust_policy(iam, infra, platform_principal_arn: str) -> TrustPolicyDiff:
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return TrustPolicyDiff(available=False, reason="role_missing")
        return TrustPolicyDiff(available=False, reason=_client_error_reason(e, "GetRole"))

    trust_doc = _decode_policy_document(role["AssumeRolePolicyDocument"])
    statements = _statements_of(trust_doc)

    covering = [s for s in statements if _statement_covers_assume_role(s)]
    narrow = [s for s in covering if _is_narrow_assume_role_statement(s, platform_principal_arn)]

    # "Exactly one" is the requirement: a second narrowly-shaped statement (e.g. a
    # duplicate, or one with a different/missing ExternalId) is exactly as much a
    # security-relevant extra as a wildcard one — prefer whichever narrow statement
    # actually carries the right ExternalId for the informational fields below, so a
    # correct-and-an-extra pairing doesn't get misreported as fully matching.
    primary = None
    if narrow:
        primary = next((s for s in narrow if _external_id_from_statement(s) == str(infra.id)), narrow[0])

    extra_statements = [_summarize_trust_statement(s) for s in covering if s is not primary]

    if primary is not None:
        principal_matches = True
        external_id = _external_id_from_statement(primary)
        external_id_present = external_id is not None
        external_id_matches = external_id == str(infra.id)
    else:
        principal_matches = False
        external_id_present = False
        external_id_matches = False

    identical = (
        primary is not None
        and principal_matches
        and external_id_present
        and external_id_matches
        and not extra_statements
    )

    return TrustPolicyDiff(
        available=True,
        principal_matches=principal_matches,
        external_id_present=external_id_present,
        external_id_matches=external_id_matches,
        extra_statements=extra_statements,
        identical=identical,
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
