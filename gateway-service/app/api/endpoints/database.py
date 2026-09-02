from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.proxy import proxy_request

router = APIRouter(prefix="/infrastructures/{infra_id}/databases", tags=["Databases"])


class DatabaseCreateBody(BaseModel):
    name: str = Field(example="primary-db", description="^[a-z][a-z0-9-]{2,30}$")
    engine: str = Field(example="postgres", description="postgres | mysql | redis | docdb")
    engine_version: str = Field(example="16.6")
    instance_class: str = Field(example="db.t3.micro")
    allocated_storage: int | None = Field(
        default=None, example=20, description="GB; required except for redis"
    )


class DatabaseResponse(BaseModel):
    id: str
    environment_id: str
    name: str
    engine: str
    engine_version: str
    instance_class: str
    allocated_storage: int | None = None
    status: str
    host: str | None = None
    port: int | None = None
    secret_arn: str | None = Field(default=None, description="AWS Secrets Manager ARN — never a credential value")
    error_message: str | None = None
    created_at: str
    updated_at: str


class DatabaseDeleteBody(BaseModel):
    confirm_name: str = Field(description="Must equal the database's name (typed-name confirmation)")


@router.get("/", summary="List databases in an infrastructure", response_model=list[DatabaseResponse])
async def database_list(infra_id: str, request: Request):
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/databases/", request,
    )


@router.post(
    "/", summary="Create a managed database", response_model=DatabaseResponse, status_code=202,
)
async def database_create(infra_id: str, body: DatabaseCreateBody, request: Request):
    """Requires the environment to be ACTIVE. Returns 422 with a refresh-script hint if
    Launchpad's IAM role in the customer account hasn't picked up the required permissions."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/databases/", request,
    )


@router.get("/{database_id}", summary="Get a database", response_model=DatabaseResponse)
async def database_get(infra_id: str, database_id: str, request: Request):
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/databases/{database_id}/", request,
    )


@router.delete(
    "/{database_id}", summary="Delete a database", response_model=DatabaseResponse, status_code=202,
)
async def database_delete(infra_id: str, database_id: str, body: DatabaseDeleteBody, request: Request):
    """Takes a final snapshot before the underlying resource is destroyed. Works from ERROR."""
    return await proxy_request(
        f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{infra_id}/databases/{database_id}/", request,
    )
