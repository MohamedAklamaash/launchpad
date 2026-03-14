from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/infrastructures", tags=["Infrastructures"])


class InfraCreateBody(BaseModel):
    name: str = Field(example="prod-infra")
    cloud_provider: str = Field(example="AWS", description="Only AWS is supported")
    max_cpu: float = Field(example=4096, description="Total CPU units ceiling across all apps (1024 = 1 vCPU)")
    max_memory: float = Field(example=8192, description="Total memory ceiling in MB across all apps")
    code: str = Field(example="123456789012", description="AWS Account ID where infrastructure will be provisioned")
    metadata: Optional[Dict[str, str]] = Field(
        default=None,
        example={"aws_region": "us-east-1", "vpc_cidr": "10.0.0.0/16"},
        description="Optional AWS-specific config"
    )

class InfraUpdateBody(BaseModel):
    name: Optional[str] = Field(default=None, example="prod-infra-v2")
    max_cpu: Optional[float] = Field(default=None, example=8192, description="New CPU units ceiling")
    max_memory: Optional[float] = Field(default=None, example=16384, description="New memory ceiling in MB")

class InfraResponse(BaseModel):
    id: str
    name: str
    cloud_provider: str
    max_cpu: float
    max_memory: float
    is_cloud_authenticated: bool = Field(description="Whether Launchpad successfully assumed the IAM role")
    code: str = Field(description="AWS Account ID")
    metadata: Dict[str, Any] = {}
    created_at: str
    updated_at: str


@router.get("/", summary="List all infrastructures for the authenticated user",
            response_model=list[InfraResponse])
async def infrastructure_list(request: Request):
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/", request)


@router.post("/", summary="Create a new infrastructure",
             response_model=InfraResponse, status_code=201)
async def infrastructure_create(body: InfraCreateBody, request: Request):
    """Triggers Terraform to provision VPC, ECS cluster, ALB, and ECR in the given AWS account."""
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/", request)


@router.get("/{infra_id}", summary="Get infrastructure details", response_model=InfraResponse)
async def infrastructure_get(infra_id: str, request: Request):
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/", request)


@router.delete("/{infra_id}", summary="Delete an infrastructure", status_code=204)
async def infrastructure_delete(infra_id: str, request: Request):
    """Triggers Terraform destroy. Returns 409 if active applications exist."""
    return await proxy_request(f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/", request)


@router.patch("/{infra_id}", summary="Update infrastructure configuration",
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
