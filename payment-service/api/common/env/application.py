from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()

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
    @classmethod
    def from_env(cls) -> "ApplicationConfig":
        return cls(
            django_secret=os.environ["DJANGO_SECRET"],
            jwt_secret=os.environ["JWT_SECRET"],
            django_port=os.environ["DJANGO_PORT"],
            internal_api_token=os.environ["INTERNAL_API_TOKEN"],
            rabbitmq_url=os.environ["RABBITMQ_URL"],
            stripe_publishable_key=os.environ["STRIPE_PUBLISHABLE_KEY"],
            stripe_secret_key=os.environ["STRIPE_SECRET_KEY"],
            stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
            frontend_url=os.environ.get("FRONTEND_URL", "http://localhost:3000")
        )

app_config = ApplicationConfig.from_env()
