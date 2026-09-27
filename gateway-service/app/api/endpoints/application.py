import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.proxy import proxy_request

router = APIRouter(prefix="/applications", tags=["Applications"])

class AppCreateBody(BaseModel):
    name: str = Field(example="my-app")
    infrastructure_id: str = Field(example="018e1234-abcd-7000-8000-000000000001")
    project_remote_url: str = Field(example="https://github.com/user/repo")
    project_branch: str | None = Field(default="main", example="main")
    description: str | None = None
    project_commit_hash: str | None = Field(default=None, example="abc1234")
    dockerfile_path: str | None = Field(default="Dockerfile", example="Dockerfile")
    port: int | None = Field(default=8080, example=8080)
    alloted_cpu: float | None = Field(default=256, example=256,
                                          description="CPU units: 256=0.25vCPU, 512=0.5vCPU, 1024=1vCPU")
    alloted_memory: float | None = Field(default=512, example=512, description="Memory in MB")
    envs: dict[str, str] | None = Field(default=None, example={"NODE_ENV": "production"})

class AppCreateResponse(BaseModel):
    id: str
    name: str

class AppDetailResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    infrastructure_id: str
    status: str = Field(description="CREATED | BUILDING | DEPLOYING | ACTIVE | SLEEPING | FAILED")
    is_sleeping: bool
    cpu: float
    memory: float
    storage: float
    port: int
    url: str
    branch: str
    dockerfile_path: str
    envs: dict[str, str] = {}
    attached_database_ids: list[str] = []
    deployment_url: str | None = None
    build_id: str | None = None
    error_message: str | None = None
    auto_deploy_paused: bool = False
    created_at: str
    updated_at: str

class AppListItem(BaseModel):
    id: str
    name: str
    cpu: float
    memory: float
    port: int

class AppUpdateBody(BaseModel):
    description: str | None = None
    envs: dict[str, str] | None = Field(default=None, example={"NODE_ENV": "production"})
    alloted_cpu: float | None = Field(default=None, description="CPU units: 256=0.25vCPU, 512=0.5vCPU, 1024=1vCPU")
    alloted_memory: float | None = Field(default=None, description="Memory in MB")
    port: int | None = None
    project_branch: str | None = None
    dockerfile_path: str | None = None
    attached_database_ids: list[str] | None = Field(
        default=None,
        description="Full replacement list of managed database ids to inject into this app. "
        "Does not trigger a redeploy — redeploy to apply.",
    )

class AppUpdateResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    envs: dict[str, str] = {}
    alloted_cpu: float
    alloted_memory: float
    port: int
    attached_database_ids: list[str] = []
    updated_at: str

class QueuedResponse(BaseModel):
    message: str
    application_id: str
    status: str = Field(example="QUEUED")

class SleepResponse(BaseModel):
    message: str
    application_id: str
    status: str = Field(example="SLEEPING")

class WakeResponse(BaseModel):
    message: str
    application_id: str
    status: str = Field(example="ACTIVE")



@router.get("/", summary="List applications for an infrastructure",
            response_model=list[AppListItem])
async def application_list(infrastructure_id: str, request: Request):
    """
    Query param `infrastructure_id` (UUID, required).
    """
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/", request)


@router.post("/", summary="Create a new application",
             response_model=AppCreateResponse, status_code=201)
async def application_create(body: AppCreateBody, request: Request):
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/", request)


@router.get("/{app_id}", summary="Get application details", response_model=AppDetailResponse)
async def application_get(app_id: str, request: Request):
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/", request)


@router.delete("/{app_id}", summary="Delete an application", status_code=204)
async def application_delete(app_id: str, request: Request):
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/", request)


@router.patch("/{app_id}/update", summary="Update application configuration",
              response_model=AppUpdateResponse)
async def application_update(app_id: str, body: AppUpdateBody, request: Request):
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/update/", request)


@router.post("/{app_id}/deploy", summary="Queue application for deployment",
             response_model=QueuedResponse, status_code=202)
async def application_deploy(app_id: str, request: Request):
    """No request body required."""
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/deploy/", request)


@router.post("/{app_id}/retry", summary="Retry a failed deployment",
             response_model=QueuedResponse, status_code=202)
async def application_retry(app_id: str, request: Request):
    """No request body required. Cleans up partial AWS resources then re-queues."""
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/retry/", request)


@router.post("/{app_id}/sleep", summary="Put application to sleep",
             response_model=SleepResponse)
async def application_sleep(app_id: str, request: Request):
    """No request body required. Scales ECS desired count to 0."""
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/sleep/", request)


@router.post("/{app_id}/wake", summary="Wake application from sleep",
             response_model=WakeResponse)
async def application_wake(app_id: str, request: Request):
    """No request body required. Restores ECS desired count."""
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/wake/", request)


class DeploymentListItem(BaseModel):
    id: str
    image_tag: str
    commit_sha: str | None = None
    compute_type: str
    status: str
    triggered_by: str
    created_at: str
    rollback_addressable: bool = True


class RollbackPreviewResponse(BaseModel):
    deployment_id: str
    image_tag: str
    commit_sha: str | None = None
    compute_type: str
    cpu: float
    memory: float
    port: int
    deployed_at: str
    added_keys: list[str] = []
    removed_keys: list[str] = []
    values_changed: bool


class RollbackQueuedResponse(BaseModel):
    message: str
    application_id: str
    deployment_id: str
    status: str = Field(example="QUEUED")


@router.get("/{app_id}/deployments", summary="List deployment history for an application",
            response_model=list[DeploymentListItem])
async def application_deployments(app_id: uuid.UUID, request: Request):
    """Owner only. Newest first; only successful deploys/rollbacks are addressable."""
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/deployments/", request)


@router.get("/{app_id}/deployments/{deployment_id}/preview", summary="Preview a rollback's config diff",
            response_model=RollbackPreviewResponse)
async def application_rollback_preview(app_id: uuid.UUID, deployment_id: uuid.UUID, request: Request):
    """Owner only. Key-name changes and a values-changed indicator — never the values themselves."""
    return await proxy_request(
        f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/deployments/{deployment_id}/preview/", request,
    )


@router.post("/{app_id}/deployments/{deployment_id}/rollback", summary="Roll back to a previous deployment",
             response_model=RollbackQueuedResponse, status_code=202)
async def application_rollback(app_id: uuid.UUID, deployment_id: uuid.UUID, request: Request):
    """Owner only. No request body. Skips CodeBuild; pauses auto-deploy until resumed."""
    return await proxy_request(
        f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/deployments/{deployment_id}/rollback/", request,
    )


@router.post("/{app_id}/resume-auto-deploy", summary="Resume auto-deploy on push")
async def application_resume_auto_deploy(app_id: uuid.UUID, request: Request):
    """Owner only. No request body. Clears the pin a rollback set."""
    return await proxy_request(
        f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/resume-auto-deploy/", request,
    )


class WebhookSecretResponse(BaseModel):
    webhook_url: str
    secret: str = Field(description="Shown only at issue/rotate time; not retrievable later")
    instructions: str


@router.post("/{app_id}/webhook-secret", summary="Issue or rotate GitHub webhook secret",
             response_model=WebhookSecretResponse)
async def application_rotate_webhook_secret(app_id: str, request: Request):
    """Generates a fresh per-app HMAC secret. The secret is returned ONCE."""
    return await proxy_request(
        f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/webhook-secret/", request,
    )


class RuntimeLogEvent(BaseModel):
    timestamp: str
    message: str


class RuntimeLogsResponse(BaseModel):
    events: list[RuntimeLogEvent]
    next_cursor: str | None = None
    truncated: bool


@router.get("/{app_id}/logs", summary="Tail an application's runtime logs",
            response_model=RuntimeLogsResponse)
async def application_logs(app_id: uuid.UUID, request: Request):
    """Owner only. Query params: `container` (app|proxy), `minutes` (1-60), `cursor`,
    `previous` (EKS only) — passed through untouched; app_id is typed as a UUID here so a
    malformed id 404s at the gateway rather than reaching the upstream as a raw string."""
    return await proxy_request(f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{app_id}/logs/", request)


# Mounted at the FastAPI app root (no /applications prefix) — GitHub URLs live under
# a stable /api/v1/webhooks/github/ path so the auth middleware can prefix-exempt them.
webhook_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@webhook_router.post("/github/{app_id}", summary="GitHub push webhook receiver", status_code=202)
async def application_github_webhook(app_id: str, request: Request):
    """
    Called by GitHub. Must include `X-GitHub-Event` and `X-Hub-Signature-256`.
    Trailing slash on upstream is required so Django doesn't 301-redirect the POST body.
    """
    return await proxy_request(
        f"{settings.APPLICATION_SERVICE_URL}/api/v1/webhooks/github/{app_id}/", request,
    )
