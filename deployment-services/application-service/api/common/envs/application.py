from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()

@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    django_secret: str
    jwt_secret: str
    django_port: int
    
    @classmethod
    def from_env(cls) -> "ApplicationConfig":
        return cls(
            django_secret=os.environ["DJANGO_SECRET"],
            jwt_secret=os.environ["JWT_SECRET"],
            django_port=os.environ["DJANGO_PORT"]
        )

app_config = ApplicationConfig.from_env()
