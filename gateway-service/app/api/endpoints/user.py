from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional, List
from app.services.proxy import proxy_request
from app.core.config import settings

router = APIRouter(prefix="/users", tags=["Users"])


class UserResponse(BaseModel):
    user_id: str
    user_name: str
    email: str
    role: str
    profile_url: Optional[str] = None
    infra_id: List[str] = []
    invited_by: Optional[str] = None
    created_at: str
    updated_at: str


@router.get("/", summary="Search users by username or email",
            response_model=List[UserResponse])
async def user_search(q: str, request: Request):
    """
    Query param `q` (required) — matched against `user_name` and `email`.
    """
    return await proxy_request(f"{settings.USER_SERVICE_URL}/api/v1/users/", request)


@router.get("/{user_id}", summary="Get a user by ID", response_model=UserResponse)
async def user_get(user_id: str, request: Request):
    return await proxy_request(f"{settings.USER_SERVICE_URL}/api/v1/users/{user_id}", request)
