import boto3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

def create_boto3_session(infrastructure):
    metadata = infrastructure.metadata or {}
    
    if not metadata.get('aws_access_key_id'):
        raise ValueError("Infrastructure not authenticated with AWS")
    
    expiration = metadata.get('expiration')
    if expiration:
        try:
            exp_time = datetime.fromisoformat(expiration.replace('Z', '+00:00'))
            if exp_time <= datetime.now(timezone.utc):
                logger.info(f"Credentials expired for infrastructure {infrastructure.id}, refreshing...")
                _refresh_credentials(infrastructure)
                metadata = infrastructure.metadata
        except Exception as e:
            logger.warning(f"Failed to parse expiration time: {e}")
    
    return boto3.Session(
        aws_access_key_id=metadata['aws_access_key_id'],
        aws_secret_access_key=metadata['aws_secret_access_key'],
        aws_session_token=metadata.get('aws_session_token'),
        region_name=metadata.get('aws_region', 'us-west-2')
    )

def _refresh_credentials(infrastructure):
    """Refresh expired AWS credentials by assuming role again."""
    from api.common.envs.application import app_config
    
    target_account_id = infrastructure.code
    
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
    )
    
    response = sts_client.assume_role(
        RoleArn=f"arn:aws:iam::{target_account_id}:role/LaunchpadDeploymentRole",
        RoleSessionName="deployment-session"
    )
    
    creds = response["Credentials"]
    metadata = infrastructure.metadata or {}
    
    infrastructure.metadata = {
        **metadata,
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
        "expiration": creds["Expiration"].isoformat() if hasattr(creds["Expiration"], 'isoformat') else str(creds["Expiration"])
    }
    infrastructure.save()
    logger.info(f"Refreshed credentials for infrastructure {infrastructure.id}")
