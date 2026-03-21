from fastapi import APIRouter, Request
from pydantic import BaseModel
from app.services.proxy import proxy_request
from app.core.config import settings

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


@router.get("/user/{user_id}", summary="Get all notifications for a user",
            response_model=list[NotificationResponse])
async def notification_list(user_id: str, request: Request):
    return await proxy_request(f"{settings.NOTIFICATION_SERVICE_URL}/api/v1/notifications/user/{user_id}", request)
