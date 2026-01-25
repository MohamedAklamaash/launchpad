from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()

def _get_bool(value: Optional[str], *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    user_name: str
    password: str
    host: str
    port: int
    name: str
    ssl: bool
    ssl_reject_unauthorized: bool
    url: str

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        return cls(
            user_name=os.environ["DATABASE_USER_NAME"],
            password=os.environ["DATABASE_PASSWORD"],
            host=os.environ.get("DATABASE_HOST", "localhost"),
            port=int(os.environ.get("DATABASE_PORT", 5432)),
            name=os.environ["DATABASE_NAME"],
            ssl=_get_bool(os.environ.get("DATABASE_SSL")),
            ssl_reject_unauthorized=_get_bool(
                os.environ.get("DATABASE_SSL_REJECT_UNAUTHORIZED"),
                default=True,
            ),
            url=os.environ["APPLICATION_DB_URL"],
        )

db_config = DatabaseConfig.from_env()