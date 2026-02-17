import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    AUTH_SERVICE_URL: str = os.getenv("AUTH_SERVICE_URL", "http://localhost:3001")
    USER_SERVICE_URL: str = os.getenv("USER_SERVICE_URL", "http://localhost:3002")
    NOTIFICATION_SERVICE_URL: str = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:3003")
    INFRASTRUCTURE_SERVICE_URL: str = os.getenv("INFRASTRUCTURE_SERVICE_URL", "http://localhost:8002")
    PAYMENT_SERVICE_URL: str = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8003")
    ALLOWED_HOSTS:str = os.getenv("ALLOWED_HOSTS", "*")
    DEBUG:bool = os.getenv("DEBUG", "True")
    PORT: int = int(os.getenv("PORT", "8000"))
    INTERNAL_API_TOKEN:str = os.getenv("INTERNAL_API_TOKEN", "")

settings = Settings()
