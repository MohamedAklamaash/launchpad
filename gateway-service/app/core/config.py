import logging
import os
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    AUTH_SERVICE_URL: str = os.getenv("AUTH_SERVICE_URL", "http://localhost:3001")
    USER_SERVICE_URL: str = os.getenv("USER_SERVICE_URL", "http://localhost:3002")
    NOTIFICATION_SERVICE_URL: str = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:3003")
    INFRASTRUCTURE_SERVICE_URL: str = os.getenv("INFRASTRUCTURE_SERVICE_URL", "http://localhost:8002")
    APPLICATION_SERVICE_URL: str = os.getenv("APPLICATION_SERVICE_URL", "http://localhost:8001")
    PAYMENT_SERVICE_URL: str = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8003")
    # Secure-by-default: a gateway sitting at the edge should not default to wildcard
    # hosts or debug-on. Production overrides via env; dev sets them explicitly.
    ALLOWED_HOSTS:str = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1")
    DEBUG:bool = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")
    PORT: int = int(os.getenv("PORT", "8000"))
    INTERNAL_API_TOKEN:str = os.getenv("INTERNAL_API_TOKEN", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    MAX_REQUESTS: int = int(os.getenv("MAX_REQUESTS", "100"))
    MAX_USER_REQUESTS: int = int(os.getenv("MAX_USER_REQUESTS", "10"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "300"))

    @field_validator(
        "AUTH_SERVICE_URL",
        "USER_SERVICE_URL",
        "NOTIFICATION_SERVICE_URL",
        "APPLICATION_SERVICE_URL",
        "INFRASTRUCTURE_SERVICE_URL",
        "PAYMENT_SERVICE_URL",
    )
    @classmethod
    def _normalize_service_url(cls, v: str, info) -> str:
        # Endpoints build URLs as f"{SERVICE_URL}/api/v1/...", so a misconfigured
        # .env with a trailing /api/v1 silently doubles the path and 404s every
        # downstream call. Strip-and-warn keeps misconfigured envs working while
        # surfacing the issue; reject other paths to fail fast on real mistakes.
        normalized = v.rstrip("/")
        parsed = urlparse(normalized)
        # A value lacking a scheme (e.g. "localhost:8002") parses with an empty netloc
        # and the host stuffed into path/scheme — that silently produces a broken base
        # URL. Require both scheme and netloc so misconfig fails fast here.
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"{info.field_name} must be a full URL with scheme and host "
                f"(e.g. http://host:port). Got: {v!r}"
            )
        path = parsed.path
        if path in ("/api/v1",):
            logger.warning(
                "%s has trailing /api/v1 — stripping. Service URLs must be host:port only.",
                info.field_name,
            )
            normalized = normalized[: -len("/api/v1")]
            path = urlparse(normalized).path
        if path not in ("",):
            raise ValueError(
                f"{info.field_name} must be a bare host:port URL (no path). Got path: {path}"
            )
        return normalized

settings = Settings()
