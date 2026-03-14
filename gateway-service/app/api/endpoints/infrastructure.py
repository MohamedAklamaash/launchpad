from fastapi import APIRouter, Request
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/infrastructures", tags=["infrastructures"])

@router.get("/")
async def infrastructure_list(request: Request):
    url = f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/"
    return await proxy_request(url, request)

@router.post("/")
async def infrastructure_create(request: Request):
    url = f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/"
    return await proxy_request(url, request)

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def infrastructure_proxy(request: Request, path: str):
    if path.rstrip('/') in ["healthz", "liveness", "readiness"]:
        url = f"{settings.INFRASTRUCTURE_SERVICE_URL}/{path}"
    else:
        url = f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{path}"
    return await proxy_request(url, request)
