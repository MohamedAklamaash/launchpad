from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import settings
from app.services.proxy import proxy_request

router = APIRouter(prefix="/users", tags=["Users"])


class UserResponse(BaseModel):
    user_id: str
    user_name: str
    email: str
    role: str
    profile_url: str | None = None
    infra_id: list[str] = []
    invited_by: str | None = None
    created_at: str
    updated_at: str


@router.get("/", summary="Search users by username or email",
            response_model=list[UserResponse])
async def user_search(q: str, request: Request):
    """
    Query param `q` (required) — matched against `user_name` and `email`.
    """
    return await proxy_request(f"{settings.USER_SERVICE_URL}/api/v1/users/", request)


@router.get("/{user_id}", summary="Get a user by ID", response_model=UserResponse)
async def user_get(user_id: str, request: Request):
    return await proxy_request(f"{settings.USER_SERVICE_URL}/api/v1/users/{user_id}", request)
