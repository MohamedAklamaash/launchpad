import logging

import boto3
from api.common.envs.application import app_config
from api.mock.aws_fixtures import synthesize_assumed_role_metadata
from api.models.infrastructure import Infrastructure
from botocore.config import Config
from botocore.exceptions import ClientError
from shared.enums.cloud_provider import CloudProvider
from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)

CREDENTIAL_KEYS = ("aws_access_key_id", "aws_secret_access_key", "aws_session_token")

SESSION_DURATION_SECONDS = 7200
FALLBACK_SESSION_DURATION_SECONDS = 3600


def _authenticate_mock_infrastructure(infrastructure: Infrastructure) -> dict:
    synthesized = synthesize_assumed_role_metadata(infrastructure)
    metadata = infrastructure.metadata or {}
    # Strip credential keys from the *existing* metadata too, not just the synthesized
    # values — a row onboarded before credentials stopped being persisted still carries
    # them, and spreading `metadata` unchanged would keep re-saving them.
    infrastructure.metadata = {
        **{k: v for k, v in metadata.items() if k not in CREDENTIAL_KEYS},
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


def _assume_role_with_fallback(infrastructure: Infrastructure) -> dict:
    """The real (non-mock) AssumeRole call, with the DurationSeconds fallback for roles
    created before the max-session-duration bump. No side effects on `infrastructure` —
    callers decide whether/how to record the outcome."""
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )
    # create_aws_role.sh raises the role's max session to 2h for EKS only (a cluster apply
    # can outlive 1h); an ECS role keeps AWS's 1h default, so asking it for 2h would be a
    # guaranteed ValidationError round trip on every assume.
    duration = SESSION_DURATION_SECONDS if infrastructure.compute_type == "eks" else FALLBACK_SESSION_DURATION_SECONDS
    try:
        response = _assume_role(sts_client, infrastructure, duration)
    except ClientError as e:
        if duration == FALLBACK_SESSION_DURATION_SECONDS or e.response.get("Error", {}).get("Code") != "ValidationError":
            raise
        # EKS roles created before the max-session-duration bump still cap at 1h; log the
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
    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
    }


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

    try:
        credentials = _assume_role_with_fallback(infrastructure)
        infrastructure.is_cloud_authenticated = True
        infrastructure.save(update_fields=["is_cloud_authenticated", "updated_at"])
        return credentials

    except Exception:
        logger.exception(
            "AssumeRole failed for infra %s", infrastructure.id
        )
        infrastructure.is_cloud_authenticated = False
        infrastructure.metadata = {**metadata, "error": "AssumeRole failed"}
        infrastructure.save(update_fields=["metadata", "is_cloud_authenticated", "updated_at"])
        raise


def assume_role_credentials_only(infrastructure: Infrastructure) -> dict:
    """Same AssumeRole path as authenticate_infrastructure, but never writes
    is_cloud_authenticated/metadata on `infrastructure` — for read-only background polling
    (F1b part 2's TLS ISSUED re-check) where a transient AssumeRole hiccup must not flip
    the customer-facing authentication status or stomp metadata that a real deploy/destroy
    call would otherwise own."""
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
        synthesized = synthesize_assumed_role_metadata(infrastructure)
        return {key: synthesized[key] for key in CREDENTIAL_KEYS}

    return _assume_role_with_fallback(infrastructure)
