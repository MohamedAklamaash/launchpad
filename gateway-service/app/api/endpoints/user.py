from fastapi import APIRouter, Path, Request
from pydantic import BaseModel

from app.core.config import settings
from app.services.proxy import proxy_request

router = APIRouter(prefix="/users", tags=["Users"])

# user-service's User.user_id is a Sequelize STRING column (not UUID) populated from
# auth-service's id at event time (identity-services/services/user-service/src/db/models/
# user.model.ts) — not a UUID-keyed resource we can safely type as uuid.UUID. Constrain the
# character set instead so a decoded '%2F', '%3F', '%23', or '..' can't reach the upstream
# URL as a raw path/query separator.
USER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"


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
async def user_get(user_id: str = Path(pattern=USER_ID_PATTERN), *, request: Request):
    return await proxy_request(f"{settings.USER_SERVICE_URL}/api/v1/users/{user_id}", request)
