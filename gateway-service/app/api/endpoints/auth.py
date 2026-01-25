from fastapi import APIRouter, Request
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])
user_router = APIRouter(prefix="/user", tags=["user_auth"])

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def auth_proxy(request: Request, path: str):
    # If the user calls /auth/user/login, it should map to /user/login in the backend
    if path.startswith("user/"):
        url = f"{settings.AUTH_SERVICE_URL}/{path}"
    else:
        url = f"{settings.AUTH_SERVICE_URL}/auth/{path}"
    return await proxy_request(url, request)

@user_router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def user_auth_proxy(request: Request, path: str):
    url = f"{settings.AUTH_SERVICE_URL}/user/{path}"
    return await proxy_request(url, request)
