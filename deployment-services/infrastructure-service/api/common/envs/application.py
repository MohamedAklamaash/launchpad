import os
from dataclasses import dataclass

from dotenv import load_dotenv
from shared.mode import normalize_mode
from shared.process_role import (
    DNS_WRITER_ROLE,
    assert_no_platform_dns_credentials,
    current_process_role,
)

load_dotenv()

# These four are the platform's cross-account keys to the kingdom: JWT_SECRET and
# INTERNAL_API_TOKEN authenticate every other service to this one, and the AWS keys
# AssumeRole into every onboarded customer account. The dns_writer process is scoped to a
# single Route53 zone in a dedicated account and must never hold any of them — if it did,
# a leak of that narrow credential would carry the same blast radius as the platform's main
# credential. Asserted absent below rather than merely unused, so a misconfigured
# deployment fails loudly instead of silently over-provisioning the writer.
DNS_WRITER_FORBIDDEN_ENV_VARS = (
    "JWT_SECRET",
    "INTERNAL_API_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
)


@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    mode: str
    process_role: str
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

    @property
    def is_dns_writer(self) -> bool:
        return self.process_role == DNS_WRITER_ROLE

    @classmethod
    def from_env(cls) -> "ApplicationConfig":
        process_role = current_process_role()
        dns_writer = process_role == DNS_WRITER_ROLE

        if dns_writer:
            present = [name for name in DNS_WRITER_FORBIDDEN_ENV_VARS if os.environ.get(name)]
            if present:
                raise RuntimeError(
                    "infrastructure-service (dns_writer role): refusing to start — these "
                    f"credentials must not be present in this process's environment: "
                    f"{', '.join(present)}"
                )
        else:
            assert_no_platform_dns_credentials("infrastructure-service")

        return cls(
            mode=normalize_mode(os.environ.get("MODE", "prod")),
            process_role=process_role,
            django_secret=os.environ["DJANGO_SECRET"],
            jwt_secret=os.environ.get("JWT_SECRET", "") if dns_writer else os.environ["JWT_SECRET"],
            django_port=os.environ["DJANGO_PORT"],
            rabbitmq_url=os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"),
            internal_api_token=(
                os.environ.get("INTERNAL_API_TOKEN", "") if dns_writer
                else os.environ["INTERNAL_API_TOKEN"]
            ),
            aws_access_key_id=(
                os.environ.get("AWS_ACCESS_KEY_ID", "") if dns_writer
                else os.environ["AWS_ACCESS_KEY_ID"]
            ),
            aws_secret_access_key=(
                os.environ.get("AWS_SECRET_ACCESS_KEY", "") if dns_writer
                else os.environ["AWS_SECRET_ACCESS_KEY"]
            ),
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