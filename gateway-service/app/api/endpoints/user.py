from fastapi import APIRouter, Request
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/users", tags=["users"])

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def user_proxy(request: Request, path: str):
    url = f"{settings.USER_SERVICE_URL}/users/{path}"
    return await proxy_request(url, request)
