import os
import subprocess
import logging
import boto3
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

TF_WORKING_DIR = Path(__file__).resolve().parent.parent.parent / "infra" / "aws"

class TerraformService:
    @staticmethod
    def _bootstrap_backend(credentials: dict, region: str) -> tuple[str, str]:
        """
        Creates shared S3 bucket and DynamoDB table for Terraform state if they don't exist.
        Returns (bucket_name, dynamodb_table_name)
        """
        bucket_name = "launchpad-tf-state"
        dynamodb_table = "launchpad-tf-locks"

        s3 = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=credentials.get("aws_access_key_id", ""),
            aws_secret_access_key=credentials.get("aws_secret_access_key", ""),
            aws_session_token=credentials.get("aws_session_token", ""),
        )

        try:
            s3.head_bucket(Bucket=bucket_name)
        except s3.exceptions.ClientError:
            if region == "us-east-1":
                s3.create_bucket(Bucket=bucket_name)
            else:
                s3.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={'LocationConstraint': region}
                )
            
            s3.put_bucket_versioning(
                Bucket=bucket_name,
                VersioningConfiguration={'Status': 'Enabled'}
            )
            s3.put_public_access_block(
                Bucket=bucket_name,
                PublicAccessBlockConfiguration={
                    'BlockPublicAcls': True,
                    'IgnorePublicAcls': True,
                    'BlockPublicPolicy': True,
                    'RestrictPublicBuckets': True
                }
            )
            logger.info(f"Created S3 state bucket: {bucket_name}")

        dynamodb = boto3.client(
            "dynamodb",
            region_name=region,
            aws_access_key_id=credentials.get("aws_access_key_id", ""),
            aws_secret_access_key=credentials.get("aws_secret_access_key", ""),
            aws_session_token=credentials.get("aws_session_token", ""),
        )

        try:
            dynamodb.describe_table(TableName=dynamodb_table)
        except dynamodb.exceptions.ResourceNotFoundException:
            dynamodb.create_table(
                TableName=dynamodb_table,
                KeySchema=[{'AttributeName': 'LockID', 'KeyType': 'HASH'}],
                AttributeDefinitions=[{'AttributeName': 'LockID', 'AttributeType': 'S'}],
                BillingMode='PAY_PER_REQUEST'
            )
            logger.info(f"Created DynamoDB lock table: {dynamodb_table}")

        return bucket_name, dynamodb_table

    @classmethod
    def run_terraform(cls, action_args: list[str], credentials: dict, env_vars: dict, environment_id: str) -> dict:
        region = env_vars.get("aws_region", "us-west-2")
        
        workspace_dir = Path(f"/tmp/terraform_workspaces/{environment_id}")
        workspace_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Using isolated Terraform workspace: {workspace_dir}")
        
        def ignore_tf(src, names):
            return [n for n in names if n in [".terraform", ".terraform.lock.hcl", "terraform.tfstate", "terraform.tfstate.backup"]]
        
        shutil.copytree(TF_WORKING_DIR, workspace_dir, dirs_exist_ok=True, ignore=ignore_tf)
        
        try:
            bucket_name, dynamodb_table = cls._bootstrap_backend(credentials, region)
            
            logger.info(f"Running terraform init with backend: bucket={bucket_name}, key=env/{environment_id}/terraform.tfstate")
            init_cmd = [
                "terraform", "init", "-no-color", "-input=false", "-reconfigure",
                f"-backend-config=bucket={bucket_name}",
                f"-backend-config=key=env/{environment_id}/terraform.tfstate",
                f"-backend-config=region={region}",
                f"-backend-config=dynamodb_table={dynamodb_table}",
                f"-backend-config=encrypt=true"
            ]
            subprocess.run(init_cmd, cwd=workspace_dir, check=True, capture_output=True, text=True)

            cmd = ["terraform"] + action_args + ["-no-color", "-input=false"]
            
            for key, val in env_vars.items():
                cmd.extend(["-var", f"{key}={val}"])

            process_env = os.environ.copy()
            
            cache_dir = Path("/tmp/terraform-provider-cache")
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            process_env.update({
                "AWS_ACCESS_KEY_ID": credentials.get("aws_access_key_id", ""),
                "AWS_SECRET_ACCESS_KEY": credentials.get("aws_secret_access_key", ""),
                "AWS_SESSION_TOKEN": credentials.get("aws_session_token", ""),
                "AWS_DEFAULT_REGION": region,
                "TF_PLUGIN_CACHE_DIR": str(cache_dir)
            })

            logger.info(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(cmd, cwd=workspace_dir, check=True, capture_output=True, text=True, env=process_env)
            
            logger.info(f"Terraform execution SUCCEEDED for environment: {environment_id}")
            return {"success": True, "output": result.stdout, "workspace_dir": str(workspace_dir)}

        except subprocess.CalledProcessError as e:
            logger.error(f"Terraform execution failed in {workspace_dir}. Exit code: {e.returncode}")
            logger.error(f"stdout: {e.stdout}")
            logger.error(f"stderr: {e.stderr}")
            return {"success": False, "error": e.stderr}
        except Exception as e:
            logger.error(f"Unexpected error during Terraform execution: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    def apply(cls, credentials: dict, config: dict, environment_id: str):
        return cls.run_terraform(["apply", "-auto-approve"], credentials, config, environment_id)

    @classmethod
    def destroy(cls, credentials: dict, config: dict, environment_id: str):
        return cls.run_terraform(["destroy", "-auto-approve"], credentials, config, environment_id)
    
    @classmethod
    def output(cls, credentials: dict, config: dict, environment_id: str):
        return cls.run_terraform(["output", "-json"], credentials, config, environment_id)
