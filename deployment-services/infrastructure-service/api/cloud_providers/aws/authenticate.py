import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from api.models.infrastructure import Infrastructure
from shared.enums.cloud_provider import CloudProvider
from shared.mode import is_dev_mode
from api.common.envs.application import app_config
from api.mock.aws_fixtures import synthesize_assumed_role_metadata

logger = logging.getLogger(__name__)

CREDENTIAL_KEYS = ("aws_access_key_id", "aws_secret_access_key", "aws_session_token")

SESSION_DURATION_SECONDS = 7200
FALLBACK_SESSION_DURATION_SECONDS = 3600


def _authenticate_mock_infrastructure(infrastructure: Infrastructure) -> dict:
    synthesized = synthesize_assumed_role_metadata(infrastructure)
    metadata = infrastructure.metadata or {}
    infrastructure.metadata = {
        **metadata,
        **{k: v for k, v in synthesized.items() if k not in CREDENTIAL_KEYS},
    }
    infrastructure.is_cloud_authenticated = True
    infrastructure.save(update_fields=["metadata", "is_cloud_authenticated", "updated_at"])
    logger.warning(
        "MOCK AssumeRole synthesized in dev mode",
        extra={"infra_id": str(infrastructure.id), "is_mock": True},
    )
    return {k: synthesized[k] for k in CREDENTIAL_KEYS}


def _assume_role(sts_client, infrastructure: Infrastructure, duration_seconds: int):
    return sts_client.assume_role(
        RoleArn=f"arn:aws:iam::{infrastructure.code}:role/LaunchpadDeploymentRole",
        RoleSessionName=f"launchpad-{infrastructure.id}",
        ExternalId=str(infrastructure.id),
        DurationSeconds=duration_seconds,
    )


def authenticate_infrastructure(infrastructure: Infrastructure) -> dict:
    if infrastructure.cloud_provider != CloudProvider.AWS:
        raise ValueError("Invalid cloud provider")

    if not infrastructure.code:
        raise ValueError("AWS Account ID is required in the infrastructure code field")

    dev_mode = is_dev_mode(app_config.mode)
    if infrastructure.is_mock and not dev_mode:
        raise ValueError("Refusing real AssumeRole against a mock infrastructure")
    if dev_mode and not infrastructure.is_mock:
        raise ValueError("Refusing mock AssumeRole against a real infrastructure")

    if infrastructure.is_mock:
        return _authenticate_mock_infrastructure(infrastructure)

    metadata = infrastructure.metadata or {}

    sts_client = boto3.client(
        "sts",
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )

    try:
        try:
            response = _assume_role(sts_client, infrastructure, SESSION_DURATION_SECONDS)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "ValidationError":
                raise
            # Roles created before the max-session-duration bump still cap at 1h; log the
            # exception type only — its message contains the role ARN.
            logger.warning(
                "AssumeRole rejected DurationSeconds=%s for infra %s (%s); retrying with %s",
                SESSION_DURATION_SECONDS,
                infrastructure.id,
                type(e).__name__,
                FALLBACK_SESSION_DURATION_SECONDS,
            )
            response = _assume_role(sts_client, infrastructure, FALLBACK_SESSION_DURATION_SECONDS)

        creds = response["Credentials"]
        infrastructure.is_cloud_authenticated = True
        infrastructure.save(update_fields=["is_cloud_authenticated", "updated_at"])
        return {
            "aws_access_key_id": creds["AccessKeyId"],
            "aws_secret_access_key": creds["SecretAccessKey"],
            "aws_session_token": creds["SessionToken"],
        }

    except Exception as e:
        logger.error(
            "AssumeRole failed for infra %s", infrastructure.id, exc_info=True
        )
        infrastructure.is_cloud_authenticated = False
        infrastructure.metadata = {**metadata, "error": "AssumeRole failed"}
        infrastructure.save(update_fields=["metadata", "is_cloud_authenticated", "updated_at"])
        raise e
