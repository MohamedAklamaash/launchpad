from typing import Any

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
    max_cpu: float = Field(example=4096, description="Total CPU units ceiling across all apps (1024 = 1 vCPU)")
    max_memory: float = Field(example=8192, description="Total memory ceiling in MB across all apps")
    code: str = Field(example="123456789012", description="AWS Account ID where infrastructure will be provisioned")
    metadata: dict[str, str] | None = Field(
        default=None,
        example={"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"},
        description="Optional AWS-specific config"
    )

class InfraUpdateBody(BaseModel):
    name: str | None = Field(default=None, example="prod-infra-v2")
    max_cpu: float | None = Field(default=None, example=8192, description="New CPU units ceiling")
    max_memory: float | None = Field(default=None, example=16384, description="New memory ceiling in MB")

class InfraResponse(BaseModel):
    id: str
    name: str
    cloud_provider: str
    max_cpu: float
    max_memory: float
    is_cloud_authenticated: bool = Field(description="Whether Launchpad successfully assumed the IAM role")
    code: str = Field(description="AWS Account ID")
    metadata: dict[str, Any] = {}
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
async def infrastructure_get(infra_id: str, request: Request):
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/", request)


@router.delete("/{infra_id}", summary="Delete an infrastructure", status_code=204)
async def infrastructure_delete(infra_id: str, request: Request):
    """Triggers Terraform destroy. Returns 409 if active applications exist."""
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/", request)


@router.patch("/{infra_id}/update", summary="Update infrastructure configuration",
              response_model=InfraResponse)
async def infrastructure_update(infra_id: str, body: InfraUpdateBody, request: Request):
    """Partial update — does not re-provision AWS resources."""
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/update/", request)


@router.delete("/{infra_id}/users/{user_id}", summary="Remove an invited user from an infrastructure",
               status_code=204)
async def infrastructure_remove_user(infra_id: str, user_id: str, request: Request):
    """Owner only. Removes the target user from the infrastructure's invited_users list."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/users/{user_id}/", request
    )


@router.post("/{infra_id}/reprovision", summary="Re-provision an infrastructure",
             status_code=202)
async def infrastructure_reprovision(infra_id: str, request: Request):
    """Resets environment status to PENDING and re-queues Terraform. Use after a failed provision or ERROR state."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/reprovision/", request
    )


class OnboardingCallbackBody(BaseModel):
    infra_id: str = Field(description="Infrastructure UUID this callback is for")
    account_id: str = Field(description="AWS Account ID where LaunchpadDeploymentRole was created")
    onboarding_token: str = Field(description="Single-use onboarding token issued at infra creation")


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
