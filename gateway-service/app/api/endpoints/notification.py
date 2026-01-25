from fastapi import APIRouter, Request
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/notifications", tags=["notifications"])

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def notification_proxy(request: Request, path: str):
    url = f"{settings.NOTIFICATION_SERVICE_URL}/notifications/{path}"
    return await proxy_request(url, request)
