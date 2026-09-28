from fastapi import APIRouter, Path, Request
from pydantic import BaseModel

from app.core.config import settings
from app.services.proxy import proxy_request

router = APIRouter(prefix="/notifications", tags=["Notifications"])

# notification-service stores user_id as an untyped Mongo string field (src/db/models/
# notification.model.ts), copied from whatever identity-service event produced it — not a
# UUID-keyed resource we can safely type as uuid.UUID. Constrain the character set instead
# so a decoded '%2F', '%3F', '%23', or '..' can't reach the upstream URL as a raw
# path/query separator.
USER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"


class NotificationResponse(BaseModel):
    id: str = None
    user_id: str
    user_name: str
    email: str
    infra_id: str
    source: str = None
    metadata: dict = {}
    created_at: int = None


@router.get("/user/{user_id}", summary="Get all notifications for a user",
            response_model=list[NotificationResponse])
async def notification_list(user_id: str = Path(pattern=USER_ID_PATTERN), *, request: Request):
    return await proxy_request(f"{settings.NOTIFICATION_SERVICE_URL}/api/v1/notifications/user/{user_id}", request)
