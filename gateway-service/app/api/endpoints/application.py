from fastapi import APIRouter, Request
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/applications", tags=["applications"])

@router.get("/")
async def application_list(request: Request):
    url = f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/"
    return await proxy_request(url, request)

@router.post("/")
async def application_create(request: Request):
    url = f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/"
    return await proxy_request(url, request)

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def application_proxy(request: Request, path: str):
    if path.rstrip('/') in ["healthz", "liveness", "readiness"]:
        url = f"{settings.APPLICATION_SERVICE_URL}/{path}"
    else:
        url = f"{settings.APPLICATION_SERVICE_URL}/api/v1/applications/{path}"
    return await proxy_request(url, request)
