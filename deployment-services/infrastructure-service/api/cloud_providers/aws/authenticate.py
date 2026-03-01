import boto3
from api.models.infrastructure import Infrastructure
from shared.enums.cloud_provider import CloudProvider
from api.common.envs.application import app_config

def authenticate_infrastructure(infrastructure: Infrastructure):
    if infrastructure.cloud_provider != CloudProvider.AWS:
        raise ValueError("Invalid cloud provider")
    
    if not infrastructure.code:
        raise ValueError("AWS Account ID is required in the infrastructure code field")

    target_account_id = infrastructure.code
    metadata = infrastructure.metadata or {}
    
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=app_config.aws_access_key_id,
        aws_secret_access_key=app_config.aws_secret_access_key,
    )

    try:
        response = sts_client.assume_role(
            RoleArn=f"arn:aws:iam::{target_account_id}:role/DeploymentRole",
            RoleSessionName="deployment-session"
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
        infrastructure.save()
        
    except Exception as e:
        infrastructure.is_cloud_authenticated = False
        infrastructure.metadata = {**metadata, "error": str(e)}
        infrastructure.save()
        raise e