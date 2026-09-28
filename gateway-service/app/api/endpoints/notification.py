from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import settings
from app.services.proxy import proxy_request

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationResponse(BaseModel):
    id: str = None
    user_id: str
    user_name: str
    email: str
    infra_id: str
    source: str = None
    metadata: dict = {}
    created_at: int = None


@router.get("/me", summary="Get all notifications for the caller",
            response_model=list[NotificationResponse])
async def notification_list(request: Request):
    """
    Scoped to the caller from the `Authorization` bearer token — there is no
    target-user parameter, so a caller can never read another user's notifications.
    """
    return await proxy_request(f"{settings.NOTIFICATION_SERVICE_URL}/api/v1/notifications/me", request)
