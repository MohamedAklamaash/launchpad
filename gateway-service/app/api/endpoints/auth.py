from fastapi import APIRouter, Request
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])
user_router = APIRouter(prefix="/user", tags=["user_auth"])

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def auth_proxy(request: Request, path: str):
    if path.rstrip('/') in ["healthz", "liveness", "readiness", "favicon.ico"]:
        url = f"{settings.AUTH_SERVICE_URL}/api/v1/{path}"
    # Specialized mapping for /auth/github -> /api/v1/user/login
    elif path == "github" or path == "github/":
        url = f"{settings.AUTH_SERVICE_URL}/api/v1/user/login"
    # Mapping for /auth/user/* -> /api/v1/user/*
    elif path.startswith("user/"):
        url = f"{settings.AUTH_SERVICE_URL}/api/v1/{path}"
    # Generic mapping for /auth/* -> /api/v1/auth/*
    else:
        url = f"{settings.AUTH_SERVICE_URL}/api/v1/auth/{path}"
    return await proxy_request(url, request)

@user_router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def user_auth_proxy(request: Request, path: str):
    # Mapping for /user/* -> /api/v1/user/*
    url = f"{settings.AUTH_SERVICE_URL}/api/v1/user/{path}"
    return await proxy_request(url, request)
