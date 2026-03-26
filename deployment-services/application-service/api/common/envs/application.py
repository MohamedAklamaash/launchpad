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
    aws_access_key_id: str
    aws_secret_access_key: str
    redis_host: str
    redis_port: int
    redis_password: str
    redis_db: int
    deployment_max_infra_worker: int

    @classmethod
    def from_env(cls) -> "ApplicationConfig":
        return cls(
            django_secret=os.environ["DJANGO_SECRET"],
            jwt_secret=os.environ["JWT_SECRET"],
            django_port=os.environ["DJANGO_PORT"],
            internal_api_token=os.environ["INTERNAL_API_TOKEN"],
            rabbitmq_url=os.environ["RABBITMQ_URL"],
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            redis_host=os.environ["REDIS_HOST"],
            redis_port=os.environ["REDIS_PORT"],
            redis_password=os.environ["REDIS_PASSWORD"],
            redis_db=os.environ["REDIS_DB"],
            deployment_max_infra_worker=os.environ["DEPLOYMENT_MAX_INFRA_WORKERS"]
        )

app_config = ApplicationConfig.from_env()