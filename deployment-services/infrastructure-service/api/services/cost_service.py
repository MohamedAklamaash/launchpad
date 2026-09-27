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
from api.repositories.infrastructure import InfrastructureRepository
from api.services.policy_errors import PolicyRefreshRequiredError
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone
from shared.aws.cost_tags import TAG_APP_KEY, TAG_INFRA_KEY
from shared.enums.orchestrator import ComputeType
from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)

# Cost Explorer is a global service reachable through a single regional API endpoint,
# independent of where the customer's own resources live.
CE_REGION = "us-east-1"

_ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException"}


def validate_window(window_start: date, window_end: date) -> None:
    if window_start >= window_end:
        raise ValueError("window_start must be before window_end")
    max_days = settings.COST_QUERY_MAX_MONTHS * 31
    if (window_end - window_start).days > max_days:
        raise ValueError(f"Query window cannot exceed {settings.COST_QUERY_MAX_MONTHS} months")
    if window_end > timezone.now().date() + timedelta(days=1):
        raise ValueError("window_end cannot be in the future")


def get_infrastructure_costs(infra, window_start: date, window_end: date) -> dict:
    """Costs for every app on `infra` between window_start (inclusive) and window_end
    (exclusive). Raises ValueError on a malformed window and PolicyRefreshRequiredError
    if the customer's applied policy predates the ce: grants (v3)."""
    validate_window(window_start, window_end)

    today = timezone.now().date()
    cached = CostReport.objects.filter(
        infrastructure=infra, window_start=window_start, window_end=window_end,
    ).first()
    if cached and cached.computed_on == today:
        return {**cached.payload, "cached": True}

    if is_dev_mode(app_config.mode) or infra.is_mock:
        payload = _mock_costs(infra, window_start, window_end)
    elif infra.compute_type == ComputeType.EKS:
        payload = _estimate_eks_costs(infra, window_start, window_end)
    else:
        payload = _actual_ecs_costs(infra, window_start, window_end)

    CostReport.objects.update_or_create(
        infrastructure=infra, window_start=window_start, window_end=window_end,
        defaults={"payload": payload, "computed_on": today},
    )
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
        if e.response.get("Error", {}).get("Code") in _ACCESS_DENIED_CODES:
            raise PolicyRefreshRequiredError(
                "Launchpad's IAM role in your AWS account is missing Cost Explorer "
                "permissions. Re-run the refresh policy script and try again.",
                denied_actions=["ce:GetCostAndUsage"],
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
        "shared": {"amount_usd": round(shared_total, 2), "source": "actual"},
        "tag_activation": _activate_cost_allocation_tags(ce),
    })
    return payload


def _activate_cost_allocation_tags(ce) -> dict:
    """Best-effort: activate the launchpad:* keys for cost allocation so future queries
    group correctly. Fails in an AWS Organizations member account — only the payer
    account can activate cost allocation tags there — in which case the caller reports
    infra-level attribution only until the payer activates them."""
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
