from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.proxy import proxy_request

router = APIRouter(prefix="/infrastructures", tags=["Infrastructures"])


class InfraCreateBody(BaseModel):
    name: str = Field(example="prod-infra")
    cloud_provider: str = Field(example="AWS", description="Only AWS is supported")
    compute_type: str | None = Field(
        default=None,
        example="ecs_fargate",
        description="Compute target: ecs_fargate (default) or eks. Immutable after creation.",
    )
    max_cpu: float = Field(example=4, description="Total vCPU ceiling across all apps")
    max_memory: float = Field(example=8, description="Total memory ceiling in GB across all apps")
    code: str = Field(example="123456789012", description="AWS Account ID where infrastructure will be provisioned")
    metadata: dict[str, str] | None = Field(
        default=None,
        example={"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"},
        description="Optional AWS-specific config"
    )

class InfraUpdateBody(BaseModel):
    name: str | None = Field(default=None, example="prod-infra-v2")
    max_cpu: float | None = Field(default=None, example=8, description="New vCPU ceiling")
    max_memory: float | None = Field(default=None, example=16, description="New memory ceiling in GB")

class InfraResponse(BaseModel):
    id: str
    name: str
    cloud_provider: str
    max_cpu: float = Field(description="Total vCPU ceiling across all apps")
    max_memory: float = Field(description="Total memory ceiling in GB across all apps")
    is_cloud_authenticated: bool = Field(description="Whether Launchpad successfully assumed the IAM role")
    code: str = Field(description="AWS Account ID")
    metadata: dict[str, Any] = {}
    platform_account_id: str = Field(description="Launchpad platform AWS account ID; the customer's trust policy must name this as principal")
    platform_user: str = Field(description="Launchpad platform IAM user name; the customer's trust policy must name this as principal")
    created_at: str
    updated_at: str


class InfraCreateResponse(InfraResponse):
    # Plaintext single-use nonce returned only on the create endpoint. Declared here so FastAPI's
    # response_model filter does not strip it before the dashboard sees it.
    onboarding_token: str | None = Field(
        default=None,
        description="Single-use onboarding token; injected into the AWS bootstrap script. Shown only once.",
    )


@router.get("/", summary="List all infrastructures for the authenticated user",
            response_model=list[InfraResponse])
async def infrastructure_list(request: Request):
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/", request)


@router.post("/", summary="Create a new infrastructure",
             response_model=InfraCreateResponse, status_code=201)
async def infrastructure_create(body: InfraCreateBody, request: Request):
    """Creates the infra row and mints a single-use onboarding token. Provisioning starts after the customer runs the bootstrap script."""
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/", request)


# Must precede the /{infra_id} routes: FastAPI matches in registration order, so the
# path parameter would otherwise capture "capabilities" as an infrastructure id.
@router.get("/capabilities", summary="Compute targets this deployment will accept")
async def list_capabilities(request: Request):
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/capabilities/", request)


@router.get("/{infra_id}", summary="Get infrastructure details", response_model=InfraResponse)
async def infrastructure_get(infra_id: UUID, request: Request):
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/", request)


class ProvisioningLogsResponse(BaseModel):
    status: str
    error_message: str | None = Field(description="Last failure reason, redacted; null when the last run succeeded")
    logs: str = Field(description="Allowlist-redacted terraform output; newest at the tail")
    withheld_lines: int
    truncated: bool = Field(description="True when the head was clipped to the storage cap")
    updated_at: str


@router.get("/{infra_id}/logs", summary="Provisioning logs for an infrastructure",
            response_model=ProvisioningLogsResponse)
async def infrastructure_logs(infra_id: UUID, request: Request):
    """Owner only — invited users get 403. Redacted terraform output plus the last failure reason."""
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/logs/", request)


@router.get("/{infra_id}/evidence-pack", summary="Compliance evidence pack for an infrastructure")
async def infrastructure_evidence_pack(infra_id: UUID, request: Request):
    """Owner only — invited users get 403. Streams a zip: rendered IAM policy, expected
    trust-policy shape, a live drift check against the customer's AWS account, and an
    honest limitations section."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/evidence-pack/", request
    )


@router.get("/{infra_id}/exit-export", summary="Exit export: a continuity handover archive")
async def infrastructure_exit_export(infra_id: UUID, request: Request):
    """Owner only — invited users get 403. Requires a JWT issued within the last few
    minutes (401 reauth_required otherwise). Streams a zip: README + revocation
    instructions, a regenerated Terraform bundle, cleaned/redacted task-definitions or
    Kubernetes manifests, the CI buildspec, and a GitHub webhook removal list. Strictly
    read-only against the customer's AWS account."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/exit-export/", request
    )


class CompleteExitBody(BaseModel):
    confirm: bool = Field(example=True)


@router.post("/{infra_id}/exit", summary="Complete exit: tear down platform DNS and mark this infrastructure exited")
async def infrastructure_complete_exit(infra_id: UUID, body: CompleteExitBody, request: Request):
    """Owner only. The one exit action that changes anything — and only in Launchpad's
    own platform DNS zone, never the customer's AWS account. Idempotent; a second call
    after success returns 409."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/exit/", request
    )


class AppCost(BaseModel):
    app: str
    amount_usd: float
    source: str = Field(description="actual | estimate | mock")


class SharedCost(BaseModel):
    amount_usd: float
    source: str = Field(description="actual | estimate | mock")
    note: str | None = None


class TagActivation(BaseModel):
    activated: bool | None
    reason: str | None


class InfraCostsResponse(BaseModel):
    infrastructure_id: str
    compute_type: str
    window_start: str
    window_end: str
    currency: str
    apps: list[AppCost]
    shared: SharedCost
    tag_activation: TagActivation
    is_mock: bool
    cached: bool


@router.get("/{infra_id}/costs", summary="Per-app cost attribution for an infrastructure",
            response_model=InfraCostsResponse)
async def infrastructure_costs(infra_id: UUID, request: Request):
    """Owner only — invited users get 403. ECS infras return Cost Explorer actuals grouped
    by app; EKS infras return a labelled estimate from pod resource requests. Accepts an
    optional `months` query param (1-3, default 1). Returns 422 with policy_refresh_required
    if the customer's applied IAM policy predates the Cost Explorer grants (v3)."""
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/costs/", request)


@router.delete("/{infra_id}", summary="Delete an infrastructure", status_code=204)
async def infrastructure_delete(infra_id: UUID, request: Request):
    """Triggers Terraform destroy. Returns 409 if active applications exist."""
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/", request)


@router.patch("/{infra_id}/update", summary="Update infrastructure configuration",
              response_model=InfraResponse)
async def infrastructure_update(infra_id: UUID, body: InfraUpdateBody, request: Request):
    """Partial update — does not re-provision AWS resources."""
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/update/", request)


@router.delete("/{infra_id}/users/{user_id}", summary="Remove an invited user from an infrastructure",
               status_code=204)
async def infrastructure_remove_user(infra_id: UUID, user_id: UUID, request: Request):
    """Owner only. Removes the target user from the infrastructure's invited_users list."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/users/{user_id}/", request
    )


@router.post("/{infra_id}/reprovision", summary="Re-provision an infrastructure",
             status_code=202)
async def infrastructure_reprovision(infra_id: UUID, request: Request):
    """Resets environment status to PENDING and re-queues Terraform. Use after a failed provision or ERROR state."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/reprovision/", request
    )


class OnboardingCallbackBody(BaseModel):
    infra_id: str = Field(description="Infrastructure UUID this callback is for")
    account_id: str = Field(description="AWS Account ID where LaunchpadDeploymentRole was created")
    onboarding_token: str = Field(description="Single-use onboarding token issued at infra creation")
    policy_version: int | None = Field(
        default=None,
        description="LaunchpadDeploymentPolicy version the script applied; absent on scripts pinned before versioning",
    )


@router.post("/onboarding/callback", summary="Onboarding callback from customer's AWS account",
             status_code=202)
async def infrastructure_onboarding_callback(body: OnboardingCallbackBody, request: Request):
    """Called by app_scripts/create_aws_role.sh after the customer creates the IAM role. No JWT required."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/onboarding/callback/", request
    )


class ScriptApiKeyResponse(BaseModel):
    api_key: str = Field(description="Per-user script API key (prefix lp_); shown only once")


@router.post("/script-api-key", summary="Issue (or rotate) the per-user script API key",
             response_model=ScriptApiKeyResponse, status_code=201)
async def script_api_key_issue(request: Request):
    """Mints the key that authenticates customer-run refreshes (create_aws_role.sh with a
    script API key) back to Launchpad. Plaintext returned once; issuing again revokes prior keys."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/script-api-key/", request
    )


class PolicyRefreshCallbackBody(BaseModel):
    account_id: str = Field(description="AWS Account ID the script ran against")
    infra_id: str | None = Field(default=None, description="Optional infra UUID to link")
    caller_arn: str | None = Field(default=None, description="sts get-caller-identity ARN of whoever ran the script")
    script: str | None = Field(default=None, example="create_aws_role.sh")
    role_name: str | None = Field(default=None)
    policy_arn: str | None = Field(default=None)
    policy_version: int | None = Field(
        default=None,
        description="LaunchpadDeploymentPolicy version the script applied; absent on scripts pinned before versioning",
    )


@router.post("/policy-refresh/callback", summary="Policy-refresh callback from customer's AWS account",
             status_code=201)
async def infrastructure_policy_refresh_callback(body: PolicyRefreshCallbackBody, request: Request):
    """Called by app_scripts/create_aws_role.sh after an attributed IAM refresh (script API key
    present). Authenticated by the per-user script API key (X-API-Key header), not a JWT — records
    who ran the refresh."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/policy-refresh/callback/", request
    )


aws_router = APIRouter(prefix="/aws", tags=["AWS"])


@aws_router.get("/regions", summary="List all available AWS regions")
async def list_aws_regions(request: Request):
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/aws/regions/", request)
