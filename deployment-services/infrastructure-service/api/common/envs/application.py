from dataclasses import dataclass
from dotenv import load_dotenv
import os

from shared.mode import normalize_mode

load_dotenv()

@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    mode: str
    django_secret: str
    jwt_secret: str
    django_port: int
    rabbitmq_url: str
    internal_api_token: str
    aws_access_key_id: str
    aws_secret_access_key: str
    redis_host: str
    redis_port: int
    redis_password: str
    redis_db: int
    infra_max_provision_workers: int
    infra_max_destroy_workers: int
    infra_shutdown_timeout: int
    infra_provision_per_destroy: int

    @classmethod
    def from_env(cls) -> "ApplicationConfig":
        return cls(
            mode=normalize_mode(os.environ.get("MODE", "prod")),
            django_secret=os.environ["DJANGO_SECRET"],
            jwt_secret=os.environ["JWT_SECRET"],
            django_port=os.environ["DJANGO_PORT"],
            rabbitmq_url=os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"),
            internal_api_token=os.environ["INTERNAL_API_TOKEN"],
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            redis_host=os.environ.get("REDIS_HOST", "localhost"),
            redis_port=int(os.environ.get("REDIS_PORT", "6379")),
            redis_password=os.environ.get("REDIS_PASSWORD", ""),
            redis_db=int(os.environ.get("REDIS_DB", "0")),
            infra_max_provision_workers=int(os.environ.get("INFRA_MAX_PROVISION_WORKERS", "5")),
            infra_max_destroy_workers=int(os.environ.get("INFRA_MAX_DESTROY_WORKERS", "3")),
            infra_shutdown_timeout=int(os.environ.get("INFRA_SHUTDOWN_TIMEOUT", "300")),
            infra_provision_per_destroy=int(os.environ.get("INFRA_PROVISION_PER_DESTROY", "1")),

        )

app_config = ApplicationConfig.from_env()