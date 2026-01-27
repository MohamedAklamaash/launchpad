from fastapi import APIRouter, Request
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/infrastructures", tags=["infrastructures"])

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def infrastructure_proxy(request: Request, path: str):
    url = f"{settings.INFRASTRUCTURE_SERVICE_URL}/api/v1/infrastructures/{path}"
    return await proxy_request(url, request)
