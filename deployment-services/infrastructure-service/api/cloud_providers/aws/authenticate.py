import logging

import boto3
from botocore.config import Config
from api.models.infrastructure import Infrastructure
from shared.enums.cloud_provider import CloudProvider
from shared.mode import is_dev_mode
from api.common.envs.application import app_config
from api.mock.aws_fixtures import synthesize_assumed_role_metadata

logger = logging.getLogger(__name__)


def _authenticate_mock_infrastructure(infrastructure: Infrastructure):
    metadata = infrastructure.metadata or {}
    infrastructure.metadata = {**metadata, **synthesize_assumed_role_metadata(infrastructure)}
    infrastructure.is_cloud_authenticated = True
    infrastructure.save(update_fields=["metadata", "is_cloud_authenticated", "updated_at"])
    logger.warning(
        "MOCK AssumeRole synthesized in dev mode",
        extra={"infra_id": str(infrastructure.id), "is_mock": True},
    )


def authenticate_infrastructure(infrastructure: Infrastructure):
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
        _authenticate_mock_infrastructure(infrastructure)
        return

    target_account_id = infrastructure.code
    metadata = infrastructure.metadata or {}
    
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )

    try:
        response = sts_client.assume_role(
            RoleArn=f"arn:aws:iam::{target_account_id}:role/LaunchpadDeploymentRole",
            RoleSessionName=f"launchpad-{infrastructure.id}",
            ExternalId=str(infrastructure.id),
        )

        creds = response["Credentials"]
        
        infrastructure.metadata = {
            **metadata,
            "aws_access_key_id": creds["AccessKeyId"],
            "aws_secret_access_key": creds["SecretAccessKey"],
            "aws_session_token": creds["SessionToken"],
            "expiration": creds["Expiration"].isoformat() if hasattr(creds["Expiration"], 'isoformat') else str(creds["Expiration"])
        }
        infrastructure.is_cloud_authenticated = True
        infrastructure.save(update_fields=["metadata", "is_cloud_authenticated", "updated_at"])

    except Exception as e:
        logger.error(
            "AssumeRole failed for infra %s", infrastructure.id, exc_info=True
        )
        infrastructure.is_cloud_authenticated = False
        infrastructure.metadata = {**metadata, "error": "AssumeRole failed"}
        infrastructure.save(update_fields=["metadata", "is_cloud_authenticated", "updated_at"])
        raise e