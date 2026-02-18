from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()

@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    django_secret: str
    jwt_secret: str
    django_port: int
    rabbitmq_url: str
    internal_api_token: str
    
    @classmethod
    def from_env(cls) -> "ApplicationConfig":
        return cls(
            django_secret=os.environ["DJANGO_SECRET"],
            jwt_secret=os.environ["JWT_SECRET"],
            django_port=os.environ["DJANGO_PORT"],
            rabbitmq_url=os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"),
            internal_api_token=os.environ["INTERNAL_API_TOKEN"]
        )

app_config = ApplicationConfig.from_env()