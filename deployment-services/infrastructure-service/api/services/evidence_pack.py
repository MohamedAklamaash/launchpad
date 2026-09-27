"""Compliance evidence pack (F5): a machine-generated, honest account of what Launchpad
can do in a customer's AWS account, what it actually has (a live drift check), and what
the policy does *not* contain.

Every claim here is rendered from data — `policy_data.py`'s notes/statements and
`terraform_worker.py`'s own module-instantiation source — never hand-authored prose that
could drift from what the platform actually does. Nothing is persisted to disk; the zip
is assembled in memory and returned to the caller.
"""
import inspect
import io
import json
import logging
import re
import uuid
import zipfile

import boto3
from api.cloud_providers.aws import iam_policy
from api.cloud_providers.aws.authenticate import authenticate_infrastructure
from api.cloud_providers.aws.iam_policy.live_diff import diff_live_policy
from api.repositories.infrastructure import InfrastructureRepository
from api.services.terraform_worker import TF_MODULES_DIR, TerraformWorker
from botocore.config import Config
from django.conf import settings
from django.utils import timezone
from shared.enums.orchestrator import ComputeType

logger = logging.getLogger(__name__)

_MODULE_SOURCE_RE = re.compile(r'source\s*=\s*"\./modules/(\w+)"')

# Why each never-instantiated module is excluded, keyed on the module directory name so
# the prose can never claim more than what `all_terraform_modules() -
# instantiated_terraform_modules()` actually finds — a module removed from
# `infra/aws/modules/` drops out of the rendered pack even if this dict still names it.
_MODULE_EXCLUSION_REASON = {
    "security": "CloudTrail + KMS logging",
    "secrets": "centralized secrets management",
    "cloud_optimizer": "cost-optimization advisories",
}

_IAM_STAR_LIMITATION_EKS = (
    "The EKS resource scoping above is defense in depth, not a containment boundary. "
    "The same policy grants `iam:*` on `*`, so the role can modify its own permissions "
    "and could reach a pre-existing cluster that way. The EKS scoping removes the "
    "one-API-call path to cluster-admin and makes any wider access require a deliberate, "
    "auditable IAM change; it does not make your existing clusters unreachable."
)

_IAM_STAR_LIMITATION_BASE = (
    "`iam:*` is granted on `*`. This grant is account-wide: the launchpad-* role naming "
    "this policy uses is a convention, not an enforced boundary, and the role can grant "
    "itself broader access than what is documented in this pack."
)

_CE_LIMITATION = (
    "This policy grants a `ce:` (Cost Explorer) action. Cost Explorer has no per-resource "
    "scoping to grant against, so this gives Launchpad visibility into the account's "
    "entire billing history, not just this infrastructure's."
)


def _modules_in(fn) -> set[str]:
    return set(_MODULE_SOURCE_RE.findall(inspect.getsource(fn)))


def _compute_type_modules(compute_type: str) -> set[str]:
    """The modules the provisioning worker emits for `compute_type` alone — an
    ecs_fargate infrastructure never gets an `eks` module and vice versa. Derived from
    the worker's own source, not hard-coded, so this can't drift from what
    `_generate_config` actually renders for that compute_type."""
    generator = TerraformWorker._generate_config_eks if compute_type == ComputeType.EKS else TerraformWorker._generate_config_ecs
    return _modules_in(generator)


def _managed_database_modules() -> set[str]:
    """Modules emitted per live Database row — present in a rendered config only when
    this infrastructure actually has one, regardless of compute_type."""
    return _modules_in(TerraformWorker._db_module_blocks)


def instantiated_terraform_modules() -> set[str]:
    """Every module name any generator path can emit, across every compute_type and
    with or without a managed database. Used only to compute what's *never*
    instantiated by anything — for what a specific infrastructure actually applies, see
    `_compute_type_modules` and `_managed_database_modules`, which don't conflate an
    ECS infrastructure with an EKS one or claim a database module nothing provisioned."""
    return _compute_type_modules(ComputeType.ECS_FARGATE) | _compute_type_modules(ComputeType.EKS) | _managed_database_modules()


def all_terraform_modules() -> set[str]:
    """Every module directory that exists under infra/aws/modules/, instantiated or not."""
    modules_dir = TF_MODULES_DIR / "modules"
    return {path.name for path in modules_dir.iterdir() if path.is_dir()}


def _has_ce_action(document: dict) -> bool:
    for statement in document.get("Statement", []):
        action = statement.get("Action") or statement.get("NotAction")
        actions = [action] if isinstance(action, str) else (action or [])
        if any(a.lower().startswith("ce:") for a in actions):
            return True
    return False


def _capability_narrative_lines() -> list[str]:
    """Bulleted capability narrative rendered straight from policy.json's own notes —
    the same text `generate.py` renders into create_aws_role.sh, so this pack can never
    say something about the policy the policy itself doesn't already say."""
    return [note if note.startswith("  ") else f"- {note}" for note in iam_policy.notes()]


def _expected_trust_policy_document(infra) -> dict:
    return {
        "Version": iam_policy.IAM_POLICY_LANGUAGE_VERSION,
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": settings.LAUNCHPAD_PLATFORM_PRINCIPAL_ARN},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"sts:ExternalId": str(infra.id)}},
            }
        ],
    }


def _iam_client_for(infra):
    credentials = authenticate_infrastructure(infra)
    return boto3.client(
        "iam",
        aws_access_key_id=credentials["aws_access_key_id"],
        aws_secret_access_key=credentials["aws_secret_access_key"],
        aws_session_token=credentials.get("aws_session_token"),
        config=Config(connect_timeout=5, read_timeout=8, retries={"max_attempts": 2}),
    )


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}


def _live_drift(infra) -> dict:
    if infra.is_mock:
        return {
            "note": "mock infrastructure — live diff unavailable",
            "policy": _unavailable("mock_infrastructure"),
            "trust_policy": _unavailable("mock_infrastructure"),
        }
    try:
        iam = _iam_client_for(infra)
        report = diff_live_policy(iam, infra, platform_principal_arn=settings.LAUNCHPAD_PLATFORM_PRINCIPAL_ARN)
    except Exception:
        # Never surface str(exc): an AssumeRole failure's message carries the role ARN,
        # same class of leak already fixed for provisioning logs and terraform stderr.
        logger.exception("Evidence pack live-diff failed for infra %s", infra.id)
        return {
            "note": "Could not assume this infrastructure's role in AWS to check live drift.",
            "policy": _unavailable("assume_role_failed"),
            "trust_policy": _unavailable("assume_role_failed"),
        }
    return {"note": None, **report.to_dict()}


def _drift_summary_markdown(drift: dict) -> str:
    if drift.get("note"):
        return drift["note"]

    lines = []
    policy = drift.get("policy", {})
    if not policy.get("available"):
        lines.append(f"- Managed policy: unavailable ({policy.get('reason')}).")
    elif policy.get("identical"):
        lines.append("- Managed policy: identical to the expected document.")
    else:
        lines.append(
            f"- Managed policy: {len(policy.get('missing_grants', []))} missing grant(s), "
            f"{len(policy.get('extra_grants', []))} extra grant(s) — see `drift.json`."
        )

    trust = drift.get("trust_policy", {})
    if not trust.get("available"):
        lines.append(f"- Trust policy: unavailable ({trust.get('reason')}).")
    elif trust.get("identical"):
        lines.append("- Trust policy: matches the expected shape (correct ExternalId and principal).")
    else:
        lines.append("- Trust policy: does not match the expected shape — see `drift.json`.")
    return "\n".join(lines)


def _excluded_modules_line(excluded: list[str]) -> str:
    labeled = ", ".join(
        f"`{m}` ({_MODULE_EXCLUSION_REASON.get(m, 'purpose not documented in this pack')})" for m in excluded
    )
    line = (
        "Terraform modules " + labeled + " exist in this codebase under "
        "`infra/aws/modules/` but no generator path ever instantiates them for any "
        "infrastructure."
    )
    logging_modules = sorted({"security", "secrets"} & set(excluded))
    if logging_modules:
        names = " and ".join(f"`{m}`" for m in logging_modules)
        verb = "are" if len(logging_modules) > 1 else "is"
        line += (
            f" In particular, {names} {verb} not applied, so this pack makes no "
            "CloudTrail, audit-logging, or centralized-secrets-management claim about "
            "your account."
        )
    return line


def _limitations_lines(
    compute_type: str, compute_type_modules: set[str], db_modules: set[str], excluded: list[str], policy_doc: dict
) -> list[str]:
    lines = ["## Honest limitations", ""]
    lines.append(f"- {_IAM_STAR_LIMITATION_EKS}" if compute_type == ComputeType.EKS else f"- {_IAM_STAR_LIMITATION_BASE}")
    if excluded:
        lines.append(f"- {_excluded_modules_line(excluded)}")
    lines.append(
        "- The modules listed as applied for this compute_type ("
        + ", ".join(f"`{m}`" for m in sorted(compute_type_modules))
        + ") are what every infrastructure of this compute_type gets, regardless of "
        "whether it has ever deployed anything. Managed-database modules ("
        + ", ".join(f"`{m}`" for m in sorted(db_modules))
        + ") are different: they are only present in this infrastructure's actual "
        "Terraform config while it has a live managed database, and disappear again once "
        "one is deleted — this pack does not check whether one currently exists."
    )
    if _has_ce_action(policy_doc):
        lines.append(f"- {_CE_LIMITATION}")
    return lines


def _render_evidence_markdown(infra, manifest: dict, drift: dict, limitations: list[str]) -> str:
    policy_version = manifest["policy_version"]
    applied = policy_version["applied"]
    lines = [
        f"# Compliance evidence pack — infrastructure {infra.id}",
        "",
        (
            f"Generated {manifest['generated_at']}. Every section below is rendered from "
            "Launchpad's IAM policy source of truth and this infrastructure's own record, "
            "not hand-authored — see `manifest.json` for the raw values."
        ),
        "",
        "## Policy version",
        "",
        f"- Current published version: v{policy_version['current']}",
        f"- Version required for compute_type `{infra.compute_type}`: v{policy_version['required_for_compute_type']}",
        f"- Version this infrastructure last reported applying: {'v' + str(applied) if applied is not None else 'never recorded'}",
        f"- Refresh required: {policy_version['refresh_required']}",
        "",
        "## What Launchpad can do in your account",
        "",
        *_capability_narrative_lines(),
        "",
        "## Trust policy",
        "",
        (
            f"Launchpad assumes `LaunchpadDeploymentRole` with `ExternalId` set to this "
            f"infrastructure's id (`{infra.id}`). See `trust-policy.expected.json` for the "
            "exact expected shape."
        ),
        "",
        "## Live drift",
        "",
        _drift_summary_markdown(drift),
        "",
        *limitations,
        "",
        "## Terraform modules applied",
        "",
        "- Applied for compute_type `" + infra.compute_type + "`: "
        + ", ".join(f"`{m}`" for m in manifest["terraform_modules"]["applied_for_compute_type"]),
        "- Applied only while a managed database exists on this infrastructure: "
        + ", ".join(f"`{m}`" for m in manifest["terraform_modules"]["applied_per_managed_database"]),
        "- Never instantiated by any generator path: "
        + ", ".join(f"`{m}`" for m in manifest["terraform_modules"]["never_instantiated"]),
        "",
    ]
    return "\n".join(lines)


def build_evidence_pack(infra) -> bytes:
    """Assemble the evidence pack zip for `infra` in memory. Never writes to disk."""
    compute_type = infra.compute_type
    policy_doc = iam_policy.document(compute_type)
    trust_doc = _expected_trust_policy_document(infra)
    drift = _live_drift(infra)
    compute_type_modules = _compute_type_modules(compute_type)
    db_modules = _managed_database_modules()
    excluded = sorted(all_terraform_modules() - instantiated_terraform_modules())

    manifest = {
        "infrastructure_id": str(infra.id),
        "compute_type": compute_type,
        "is_mock": infra.is_mock,
        "generated_at": timezone.now().isoformat(),
        "policy_version": {
            "current": iam_policy.version(),
            "applied": infra.policy_version,
            "required_for_compute_type": iam_policy.required_version_for(compute_type),
            "refresh_required": infra.policy_refresh_required(),
        },
        "terraform_modules": {
            "applied_for_compute_type": sorted(compute_type_modules),
            "applied_per_managed_database": sorted(db_modules),
            "never_instantiated": excluded,
        },
    }
    limitations = _limitations_lines(compute_type, compute_type_modules, db_modules, excluded, policy_doc)
    evidence_md = _render_evidence_markdown(infra, manifest, drift, limitations)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("EVIDENCE.md", evidence_md)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("policy.expected.json", iam_policy.document_json(compute_type))
        zf.writestr("trust-policy.expected.json", json.dumps(trust_doc, indent=2))
        zf.writestr("drift.json", json.dumps(drift, indent=2))
    return buf.getvalue()


class EvidencePackService:
    def __init__(self):
        self.infra_repo = InfrastructureRepository()

    def get_pack(self, user_id, infra_id) -> tuple[bytes, "object"]:
        """Returns (zip_bytes, infra) so the view can log/name the download without a
        second lookup."""
        infra = self._get_owned_infra(user_id, infra_id)
        return build_evidence_pack(infra), infra

    def _get_owned_infra(self, user_id, infra_id):
        # A malformed id reaching filter(id=...) on a UUIDField raises
        # django.core.exceptions.ValidationError — not a ValueError — and would escape
        # the view's error mapping as a 500.
        try:
            uuid.UUID(str(infra_id))
        except ValueError:
            raise LookupError("Infrastructure not found")
        infra = self.infra_repo.get_by_id(user_id, infra_id)
        if not infra:
            raise LookupError("Infrastructure not found")
        if str(infra.user_id) != str(user_id):
            raise PermissionError("Only the infrastructure owner can download the evidence pack")
        return infra
