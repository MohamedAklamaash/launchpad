import boto3
from botocore.config import Config
import logging
import os
from datetime import datetime, timezone, timedelta
from api.common.envs.application import app_config

logger = logging.getLogger(__name__)

BOTO3_CONFIG = Config(
    retries={
        'max_attempts': int(os.environ.get('AWS_MAX_RETRY_ATTEMPTS', '10')),
        'mode': os.environ.get('AWS_RETRY_MODE', 'adaptive')
    },
    connect_timeout=int(os.environ.get('AWS_CONNECT_TIMEOUT', '10')),
    read_timeout=int(os.environ.get('AWS_READ_TIMEOUT', '60'))
)

# Refresh credentials this many minutes before expiry to avoid mid-deploy expiration.
# STS tokens are typically 1 hour; 10 min buffer gives ample time for a full deploy.
_CREDENTIAL_REFRESH_BUFFER_MINUTES = int(os.environ.get('AWS_CREDENTIAL_REFRESH_BUFFER_MINUTES', '10'))

# Per-infra last-refresh timestamps to rate-limit STS calls (max once per 5 min)
_last_refresh: dict = {}
_REFRESH_RATE_LIMIT_SECONDS = 300


def create_boto3_session(infrastructure):
    metadata = infrastructure.metadata or {}

    if not metadata.get('aws_access_key_id'):
        raise ValueError("Infrastructure not authenticated with AWS")

    expiration = metadata.get('expiration')
    if expiration:
        try:
            exp_time = datetime.fromisoformat(expiration.replace('Z', '+00:00'))
            if exp_time <= datetime.now(timezone.utc) + timedelta(minutes=_CREDENTIAL_REFRESH_BUFFER_MINUTES):
                _refresh_credentials(infrastructure)
                metadata = infrastructure.metadata
        except Exception as e:
            logger.warning(f"Failed to check credential expiration for {infrastructure.id}: {e}")

    return boto3.Session(
        aws_access_key_id=metadata['aws_access_key_id'],
        aws_secret_access_key=metadata['aws_secret_access_key'],
        aws_session_token=metadata.get('aws_session_token'),
        region_name=metadata.get('aws_region', 'us-west-2')
    )


def get_boto3_config():
    return BOTO3_CONFIG


def _refresh_credentials(infrastructure):
    """Refresh AWS STS credentials with rate limiting and timeout."""
    infra_id = str(infrastructure.id)
    now = datetime.now(timezone.utc).timestamp()

    last = _last_refresh.get(infra_id, 0)
    if now - last < _REFRESH_RATE_LIMIT_SECONDS:
        logger.info(f"Skipping credential refresh for {infra_id} — rate limited (last refresh {int(now - last)}s ago)")
        return

    target_account_id = infrastructure.code
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
        config=Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 2}),
    )

    response = sts_client.assume_role(
        RoleArn=f"arn:aws:iam::{target_account_id}:role/LaunchpadDeploymentRole",
        RoleSessionName="deployment-session"
    )

    creds = response["Credentials"]
    infrastructure.metadata = {
        **(infrastructure.metadata or {}),
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
        "expiration": creds["Expiration"].isoformat() if hasattr(creds["Expiration"], 'isoformat') else str(creds["Expiration"])
    }
    infrastructure.save()
    _last_refresh[infra_id] = now
    logger.info(f"Refreshed credentials for infrastructure {infra_id}")
