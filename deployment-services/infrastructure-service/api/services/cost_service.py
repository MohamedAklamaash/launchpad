"""Per-app cost attribution (F4).

ECS infrastructures get real Cost Explorer actuals grouped by the `launchpad:app` cost
allocation tag (`source: "actual"`). EKS infrastructures get a labelled estimate from pod
resource requests, since a Kubernetes pod isn't a taggable AWS resource and Cost Explorer
cannot see inside a cluster (`source: "estimate"` on every figure). An infrastructure is
one compute_type or the other, never both, so a single query never mixes actual and
estimated app figures.

Cost Explorer bills the customer $0.01 per GetCostAndUsage call and lags real spend by
~24h, so results are cached per (infrastructure, window) per day — see CostReport.
"""
import hashlib
import logging
import uuid
from datetime import date, timedelta

import boto3
from api.cloud_providers.aws.authenticate import authenticate_infrastructure
from api.common.envs.application import app_config
from api.models import Application, CostReport
from api.models.infrastructure import Infrastructure
from api.repositories.infrastructure import InfrastructureRepository
from api.services.policy_errors import PolicyRefreshRequiredError
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from shared.aws.cost_tags import TAG_APP_KEY, TAG_INFRA_KEY
from shared.enums.orchestrator import ComputeType
from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)

# Cost Explorer is a global service reachable through a single regional API endpoint,
# independent of where the customer's own resources live.
CE_REGION = "us-east-1"

_ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException"}
# Transient on AWS's side — the customer's own request rate, or CE's own data pipeline
# lag. Retrying the refresh policy script would not fix either; the caller should retry
# the request itself later.
_RETRY_LATER_CODES = {"LimitExceededException", "ThrottlingException", "DataUnavailableException"}
# CE rejects a tag-based Filter/GroupBy outright when the tag key isn't recognized for
# cost allocation yet — most likely because it was activated less than ~24h ago, or
# activation hasn't happened. Distinct from an IAM permissions gap.
_TAG_NOT_ACTIVATED_CODES = {"ValidationException"}


class CostExplorerTemporarilyUnavailableError(Exception):
    """Cost Explorer signaled a transient condition (rate limit, data not yet
    available). Maps to 503 — re-running the refresh policy script would not help."""


class CostExplorerNotEnabledError(ValueError):
    """The account has never opened Cost Explorer. AWS returns AccessDeniedException
    ("User not enabled for cost explorer access") even though the IAM grant is present,
    so this must not be reported as a missing permission."""


class CostAllocationTagsNotActivatedError(ValueError):
    """Cost Explorer rejected the tag-based query, most likely because launchpad:app/
    launchpad:infra aren't activated for cost allocation yet. Distinguished from
    PolicyRefreshRequiredError: the IAM grant is fine, the tags just aren't ready."""


def validate_window(window_start: date, window_end: date) -> None:
    if window_start >= window_end:
        raise ValueError("window_start must be before window_end")
    max_days = settings.COST_QUERY_MAX_MONTHS * 31
    if (window_end - window_start).days > max_days:
        raise ValueError(f"Query window cannot exceed {settings.COST_QUERY_MAX_MONTHS} months")
    if window_end > timezone.now().date() + timedelta(days=1):
        raise ValueError("window_end cannot be in the future")


def _cached_report(infra, window_start: date, window_end: date, today: date):
    cached = CostReport.objects.filter(
        infrastructure=infra, window_start=window_start, window_end=window_end,
    ).first()
    if cached and cached.computed_on == today:
        return cached.payload
    return None


def get_infrastructure_costs(infra, window_start: date, window_end: date) -> dict:
    """Costs for every app on `infra` between window_start (inclusive) and window_end
    (exclusive). Raises ValueError on a malformed window and PolicyRefreshRequiredError
    if the customer's applied policy predates the ce: grants (v3)."""
    validate_window(window_start, window_end)

    today = timezone.now().date()
    hit = _cached_report(infra, window_start, window_end, today)
    if hit is not None:
        return {**hit, "cached": True}

    # Serialize concurrent misses for the same infra: without this, two requests that
    # both miss the cache would each call Cost Explorer (each GetCostAndUsage call costs
    # the customer $0.01) and race on CostReport's unique (infrastructure, window)
    # constraint. select_for_update is a no-op on backends without row locking (e.g.
    # SQLite in tests) rather than an error, so this degrades safely there.
    with transaction.atomic():
        Infrastructure.objects.select_for_update().get(pk=infra.pk)

        # Re-check under the lock: whoever got here first may have already computed and
        # cached this exact window while this request waited for the lock.
        hit = _cached_report(infra, window_start, window_end, today)
        if hit is not None:
            return {**hit, "cached": True}

        if is_dev_mode(app_config.mode) or infra.is_mock:
            payload = _mock_costs(infra, window_start, window_end)
        elif infra.compute_type == ComputeType.EKS:
            payload = _estimate_eks_costs(infra, window_start, window_end)
        else:
            payload = _actual_ecs_costs(infra, window_start, window_end)

        try:
            CostReport.objects.update_or_create(
                infrastructure=infra, window_start=window_start, window_end=window_end,
                defaults={"payload": payload, "computed_on": today},
            )
        except IntegrityError:
            # Should not happen under the lock above; kept as a backstop so a race we
            # didn't anticipate serves the report that won instead of a 500 after the
            # CE call has already been paid for.
            hit = _cached_report(infra, window_start, window_end, today)
            if hit is None:
                raise
            return {**hit, "cached": True}

        # window_end is always "today", so the (infra, window) key changes every day this
        # infra's costs are queried — without this, a polled infra accumulates one row per
        # day forever. A row from a previous day can never be a cache hit again (the check
        # above requires computed_on == today), so it's safe to drop; same-day rows for a
        # different `months` selection stay cached.
        CostReport.objects.filter(infrastructure=infra).exclude(computed_on=today).delete()

    return {**payload, "cached": False}


def _base_payload(infra, window_start: date, window_end: date) -> dict:
    return {
        "infrastructure_id": str(infra.id),
        "compute_type": infra.compute_type,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "currency": "USD",
        "is_mock": False,
    }


# ── mock ─────────────────────────────────────────────────────────────────────────

def _mock_costs(infra, window_start: date, window_end: date) -> dict:
    """Deterministic fake figures for is_mock infras / LAUNCHPAD_MOCK dev mode — never
    touches AWS. Derived from the full infra/app id (never a slice — CLAUDE.md)."""
    days = (window_end - window_start).days
    scale = days / 30

    apps = []
    for app in Application.objects.filter(infrastructure_id=str(infra.id)).order_by("id"):
        seed = int(hashlib.sha256(f"{infra.id}:{app.id}".encode()).hexdigest()[:8], 16)
        apps.append({"app": app.name, "amount_usd": round((seed % 5000) / 100 * scale, 2), "source": "mock"})

    shared_seed = int(hashlib.sha256(str(infra.id).encode()).hexdigest()[:8], 16)
    payload = _base_payload(infra, window_start, window_end)
    payload.update({
        "apps": apps,
        "shared": {"amount_usd": round((shared_seed % 2000) / 100 * scale, 2), "source": "mock"},
        "tag_activation": {"activated": None, "reason": "mock_infrastructure"},
        "is_mock": True,
    })
    return payload


# ── ECS actuals ──────────────────────────────────────────────────────────────────

def _ce_client(infra):
    credentials = authenticate_infrastructure(infra)
    return boto3.client(
        "ce",
        region_name=CE_REGION,
        aws_access_key_id=credentials["aws_access_key_id"],
        aws_secret_access_key=credentials["aws_secret_access_key"],
        aws_session_token=credentials.get("aws_session_token"),
        config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
    )


def _actual_ecs_costs(infra, window_start: date, window_end: date) -> dict:
    ce = _ce_client(infra)

    try:
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": window_start.isoformat(), "End": window_end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            Filter={"Tags": {"Key": TAG_INFRA_KEY, "Values": [str(infra.id)]}},
            GroupBy=[{"Type": "TAG", "Key": TAG_APP_KEY}],
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in _ACCESS_DENIED_CODES and "not enabled for cost explorer" in e.response.get("Error", {}).get("Message", "").lower():
            raise CostExplorerNotEnabledError(
                "AWS Cost Explorer is not enabled for this account. Open Billing and Cost "
                "Management → Cost Explorer once in the AWS console to enable it; data can "
                "take up to 24 hours to appear."
            ) from e
        if code in _ACCESS_DENIED_CODES:
            raise PolicyRefreshRequiredError(
                "Launchpad's IAM role in your AWS account is missing Cost Explorer "
                "permissions. Re-run the refresh policy script and try again.",
                denied_actions=["ce:GetCostAndUsage"],
            ) from e
        if code in _TAG_NOT_ACTIVATED_CODES:
            raise CostAllocationTagsNotActivatedError(
                "AWS Cost Explorer rejected the tagged cost query. The launchpad:app/"
                "launchpad:infra cost allocation tags may not be activated yet — a tag "
                "typically becomes activatable about 24 hours after its first use."
            ) from e
        if code in _RETRY_LATER_CODES:
            raise CostExplorerTemporarilyUnavailableError(
                f"AWS Cost Explorer is temporarily unable to serve this request ({code}). "
                "Try again shortly."
            ) from e
        raise

    app_totals: dict[str, float] = {}
    shared_total = 0.0
    for result in response.get("ResultsByTime", []):
        for group in result.get("Groups", []):
            # Group keys render as "launchpad:app$<value>"; an empty value (untagged
            # spend, e.g. the shared ALB/NAT/cluster) is the "launchpad:app$" case.
            raw_key = group["Keys"][0]
            tag_value = raw_key.split("$", 1)[1] if "$" in raw_key else ""
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if tag_value:
                app_totals[tag_value] = app_totals.get(tag_value, 0.0) + amount
            else:
                shared_total += amount

    apps = [
        {"app": name, "amount_usd": round(amount, 2), "source": "actual"}
        for name, amount in sorted(app_totals.items())
    ]

    payload = _base_payload(infra, window_start, window_end)
    payload.update({
        "apps": apps,
        "shared": {
            "amount_usd": round(shared_total, 2),
            "source": "actual",
            # Terraform's default_tags now sets launchpad:infra on every resource it
            # manages (VPC, ALB, NAT gateway, ECS cluster) in addition to the CodeBuild
            # project — but only once that infra's next terraform apply/reconcile has run
            # with the updated config. An infra provisioned before that carries only the
            # older InfraID tag on those resources until then, so this figure covers just
            # the CodeBuild project for it in the meantime.
            "note": "May undercount ALB/NAT/VPC/cluster spend until this infrastructure's next terraform reconcile applies the launchpad:infra tag to them",
        },
        "tag_activation": _activate_cost_allocation_tags(ce),
    })
    return payload


def _activate_cost_allocation_tags(ce) -> dict:
    """Activate the launchpad:* keys for cost allocation so future queries group
    correctly. Checks current status first via ListCostAllocationTags and skips the write
    when both keys are already Active — UpdateCostAllocationTagsStatus is a billing-config
    write, not a read, and this call runs on every cache miss. Fails in an AWS
    Organizations member account — only the payer account can activate cost allocation
    tags there — in which case the caller reports infra-level attribution only until the
    payer activates them."""
    try:
        existing = ce.list_cost_allocation_tags(TagKeys=[TAG_INFRA_KEY, TAG_APP_KEY])
        statuses = {t["TagKey"]: t.get("Status") for t in existing.get("CostAllocationTags", [])}
        if statuses.get(TAG_INFRA_KEY) == "Active" and statuses.get(TAG_APP_KEY) == "Active":
            return {"activated": True, "reason": None}
    except ClientError as e:
        logger.warning(
            "Could not list cost allocation tag status, will still attempt activation: %s",
            e.response.get("Error", {}).get("Code", ""),
        )

    try:
        ce.update_cost_allocation_tags_status(
            CostAllocationTagsStatus=[
                {"TagKey": TAG_INFRA_KEY, "Status": "Active"},
                {"TagKey": TAG_APP_KEY, "Status": "Active"},
            ]
        )
        return {"activated": True, "reason": None}
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.warning("Could not activate cost allocation tags for cost attribution: %s", code)
        reason = "payer_account_required" if code in _ACCESS_DENIED_CODES else "activation_failed"
        return {"activated": False, "reason": reason}


# ── EKS estimate ─────────────────────────────────────────────────────────────────

def _estimate_eks_costs(infra, window_start: date, window_end: date) -> dict:
    """Estimate from each app's pod resource request (alloted_cpu/alloted_memory, the
    same values the deployer set the pod's resource requests to) times published
    Fargate on-demand pricing, times the full window — deliberately coarse (see
    Application model: deploy-time status isn't synced here), always source="estimate"."""
    hours = (window_end - window_start).days * 24

    apps = []
    for app in Application.objects.filter(infrastructure_id=str(infra.id)).order_by("id"):
        cpu_cost = app.alloted_cpu * settings.COST_ESTIMATE_VCPU_HOUR_USD * hours
        memory_cost = app.alloted_memory * settings.COST_ESTIMATE_GB_HOUR_USD * hours
        apps.append({"app": app.name, "amount_usd": round(cpu_cost + memory_cost, 2), "source": "estimate"})

    control_plane_cost = settings.COST_ESTIMATE_EKS_CONTROL_PLANE_HOUR_USD * hours

    payload = _base_payload(infra, window_start, window_end)
    payload.update({
        "apps": apps,
        "shared": {
            "amount_usd": round(control_plane_cost, 2),
            "source": "estimate",
            "note": "EKS control plane hourly rate; node/Fargate compute is counted per app above",
        },
        "tag_activation": {"activated": None, "reason": "eks_estimate_only"},
    })
    return payload


# ── owner-only entrypoint ────────────────────────────────────────────────────────

class CostService:
    def __init__(self):
        self.infra_repo = InfrastructureRepository()

    def get_costs(self, user_id, infra_id, months: int) -> dict:
        infra = self._get_owned_infra(user_id, infra_id)

        if not isinstance(months, int) or not (1 <= months <= settings.COST_QUERY_MAX_MONTHS):
            raise ValueError(f"months must be an integer between 1 and {settings.COST_QUERY_MAX_MONTHS}")

        window_end = timezone.now().date()
        window_start = window_end - timedelta(days=30 * months)
        return get_infrastructure_costs(infra, window_start, window_end)

    def _get_owned_infra(self, user_id, infra_id):
        # A malformed id reaching filter(id=...) on a UUIDField raises
        # django.core.exceptions.ValidationError — not a ValueError — and would escape as a 500.
        try:
            uuid.UUID(str(infra_id))
        except ValueError:
            raise LookupError("Infrastructure not found")
        infra = self.infra_repo.get_by_id(user_id, infra_id)
        if not infra:
            raise LookupError("Infrastructure not found")
        if str(infra.user_id) != str(user_id):
            raise PermissionError("Only the infrastructure owner can view its costs")
        return infra
