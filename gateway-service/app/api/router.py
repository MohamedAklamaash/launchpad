from fastapi import APIRouter
from app.api.endpoints import auth, user, notification, infrastructure, payment, application

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(auth.user_router)
api_router.include_router(user.router)
api_router.include_router(notification.router)
api_router.include_router(infrastructure.router)
api_router.include_router(payment.router)
api_router.include_router(application.router)
