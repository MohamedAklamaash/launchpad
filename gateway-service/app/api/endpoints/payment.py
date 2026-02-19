from fastapi import APIRouter, Request
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/payments", tags=["payments"])

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def payment_proxy(request: Request, path: str):
    if path.rstrip('/') in ["healthz", "liveness", "readiness"]:
        url = f"{settings.PAYMENT_SERVICE_URL}/{path}"
    else:
        url = f"{settings.PAYMENT_SERVICE_URL}/payments/{path}"
    return await proxy_request(url, request)
