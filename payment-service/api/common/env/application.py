import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _origin_only(raw: str) -> str:
    """Reduce a URL to scheme://host[:port]. Stripe success/cancel URLs are built as
    f"{public_backend_url}/api/v1/...", so a trailing path/slash yields // or doubled
    paths in the redirect."""
    parsed = urlparse((raw or "").strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return (raw or "").strip().rstrip("/")

@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    django_secret: str
    jwt_secret: str
    django_port: int
    internal_api_token: str
    rabbitmq_url: str

    stripe_publishable_key: str
    stripe_secret_key: str
    stripe_webhook_secret: str
    frontend_url: str
    backend_url: str
    public_backend_url: str

    # F9 ticket 5: backs OrgPermissions's role cache (shared.services.org_role_cache) —
    # this service never used Redis before this ticket. Optional with safe defaults
    # (mirrors infrastructure-service's non-required style) so an existing deployment's
    # env doesn't need to change for OrgPermissions to work at all; a Redis outage or an
    # unconfigured instance both just fall back to the authoritative DB read, never to
    # "allow" (see org_role_cache.get_cached_role).
    redis_host: str
    redis_port: int
    redis_password: str
    redis_db: int

    @classmethod
    def from_env(cls) -> "ApplicationConfig":
        stripe_webhook_secret = os.environ["STRIPE_WEBHOOK_SECRET"]
        if stripe_webhook_secret.startswith("whsec_REPLACE"):
            raise ValueError(
                "STRIPE_WEBHOOK_SECRET is set to placeholder value. "
                "Replace it with your real Stripe webhook signing secret."
            )

        return cls(
            django_secret=os.environ["DJANGO_SECRET"],
            jwt_secret=os.environ["JWT_SECRET"],
            django_port=os.environ["DJANGO_PORT"],
            internal_api_token=os.environ["INTERNAL_API_TOKEN"],
            rabbitmq_url=os.environ["RABBITMQ_URL"],
            stripe_publishable_key=os.environ["STRIPE_PUBLISHABLE_KEY"],
            stripe_secret_key=os.environ["STRIPE_SECRET_KEY"],
            stripe_webhook_secret=stripe_webhook_secret,
            frontend_url=os.environ.get("FRONTEND_URL", "http://localhost:3000"),
            backend_url=os.environ.get("BACKEND_URL", "http://localhost:8003"),
            public_backend_url=_origin_only(os.environ["PUBLIC_BACKEND_URL"]),
            redis_host=os.environ.get("REDIS_HOST", "localhost"),
            redis_port=int(os.environ.get("REDIS_PORT", "6379")),
            redis_password=os.environ.get("REDIS_PASSWORD", ""),
            redis_db=int(os.environ.get("REDIS_DB", "0")),
        )

app_config = ApplicationConfig.from_env()
