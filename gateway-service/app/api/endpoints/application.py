from fastapi import APIRouter, Request
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/applications", tags=["applications"])

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def application_proxy(request: Request, path: str):
    # Route health checks to the correct backend path
    if path.rstrip('/') in ["healthz", "liveness", "readiness"]:
        url = f"{settings.APPLICATION_SERVICE_URL}/{path}"
    else:
        url = f"{settings.APPLICATION_SERVICE_URL}/applications/{path}"
    return await proxy_request(url, request)
