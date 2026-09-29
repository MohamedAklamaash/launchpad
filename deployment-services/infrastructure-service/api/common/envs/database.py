import os
from dataclasses import dataclass

from dotenv import load_dotenv
from shared.process_role import is_dns_writer_role

# See application.py: the dns_writer must not inherit the service's shared .env.
if not is_dns_writer_role():
    load_dotenv()


def _get_bool(value: str | None, *, default: bool = False) -> bool:
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
        # The dns_writer role connects with its own least-privilege DB role — SELECT only
        # on the tables it reads (infrastructure, environment, infrastructurecertificate),
        # read/write only on api_platformdnsrecord (the ledger) — rather than whatever
        # broader grants DATABASE_USER_NAME carries for the web/worker processes. See
        # api/services/platform_dns/sql/dns_writer_grants.sql for the exact GRANT
        # statements and docs/PLATFORM_DNS_ISOLATION.md for how to apply them.
        if is_dns_writer_role():
            user_name = os.environ.get("DNS_WRITER_DB_USER")
            password = os.environ.get("DNS_WRITER_DB_PASSWORD")
            if not user_name or not password:
                from shared.mode import is_dev_mode
                if not is_dev_mode(os.environ.get("MODE", "prod")):
                    raise RuntimeError(
                        "infrastructure-service (dns_writer role): refusing to start in "
                        "production without DNS_WRITER_DB_USER/DNS_WRITER_DB_PASSWORD — "
                        "see api/services/platform_dns/sql/dns_writer_grants.sql"
                    )
                # Dev/local convenience only: falls back to the shared dev credential so
                # `manage.py run_dns_writer` works against a docker-compose Postgres that
                # hasn't had the least-privilege role provisioned yet.
                user_name = user_name or os.environ["DATABASE_USER_NAME"]
                password = password or os.environ["DATABASE_PASSWORD"]
        else:
            user_name = os.environ["DATABASE_USER_NAME"]
            password = os.environ["DATABASE_PASSWORD"]

        return cls(
            user_name=user_name,
            password=password,
            host=os.environ.get("DATABASE_HOST", "localhost"),
            port=int(os.environ.get("DATABASE_PORT", "5432")),
            name=os.environ["DATABASE_NAME"],
            ssl=_get_bool(os.environ.get("DATABASE_SSL")),
            ssl_reject_unauthorized=_get_bool(
                os.environ.get("DATABASE_SSL_REJECT_UNAUTHORIZED"),
                default=True,
            ),
            url=os.environ["INFRASTRUCTURE_DB_URL"],
        )
