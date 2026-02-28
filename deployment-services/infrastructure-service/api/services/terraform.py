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
    def _bootstrap_backend(credentials: dict, region: str, env_name: str) -> str:
        """
        Creates an S3 bucket in the user's account to store terraform state if it doesn't exist.
        """
        sts = boto3.client(
            "sts",
            aws_access_key_id=credentials.get("aws_access_key_id", ""),
            aws_secret_access_key=credentials.get("aws_secret_access_key", ""),
            aws_session_token=credentials.get("aws_session_token", ""),
        )
        account_id = sts.get_caller_identity().get("Account")
        bucket_name = f"launchpad-tf-state-{account_id}-{region}"

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
            
            # Enable versioning
            s3.put_bucket_versioning(
                Bucket=bucket_name,
                VersioningConfiguration={'Status': 'Enabled'}
            )
            logger.info(f"Created S3 state bucket: {bucket_name}")

        return bucket_name

    @classmethod
    def run_terraform(cls, action_args: list[str], credentials: dict, env_vars: dict) -> dict:
        region = env_vars.get("aws_region", "us-west-2")
        env_name = env_vars.get("environment", "default")
        
        with tempfile.TemporaryDirectory(prefix=f"tf-{env_name}-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            logger.info(f"Using isolated Terraform working directory: {tmp_path}")
            
            shutil.copytree(TF_WORKING_DIR, tmp_path, dirs_exist_ok=True)
            
            try:
                bucket_name = cls._bootstrap_backend(credentials, region, env_name)
                
                logger.info(f"Running terraform init with backend bucket: {bucket_name}")
                init_cmd = [
                    "terraform", "init", "-no-color", "-input=false",
                    f"-backend-config=bucket={bucket_name}",
                    f"-backend-config=key={env_name}/terraform.tfstate",
                    f"-backend-config=region={region}"
                ]
                subprocess.run(init_cmd, cwd=tmp_path, check=True, capture_output=True, text=True)

                cmd = ["terraform"] + action_args + ["-no-color", "-input=false"]
                
                for key, val in env_vars.items():
                    cmd.extend(["-var", f"{key}={val}"])

                process_env = os.environ.copy()
                
                # Use a shared provider cache to avoid downloading plugins for every run
                cache_dir = Path("/tmp/terraform-provider-cache")
                cache_dir.mkdir(parents=True, exist_ok=True)
                
                process_env.update({
                    "AWS_ACCESS_KEY_ID": credentials.get("aws_access_key_id", ""),
                    "AWS_SECRET_ACCESS_KEY": credentials.get("aws_secret_access_key", ""),
                    "AWS_SESSION_TOKEN": credentials.get("aws_session_token", ""),
                    "AWS_DEFAULT_REGION": env_vars.get("aws_region", "us-west-2"),
                    "TF_PLUGIN_CACHE_DIR": str(cache_dir)
                })

                logger.info(f"Running command: {' '.join(cmd)}")
                result = subprocess.run(cmd, cwd=tmp_path, check=True, capture_output=True, text=True, env=process_env)
                
                logger.info(f"Terraform execution SUCCEEDED for infra: {env_name}")
                return {"success": True, "output": result.stdout}

            except subprocess.CalledProcessError as e:
                logger.error(f"Terraform execution failed in {tmp_path}. Exit code: {e.returncode}")
                logger.error(f"stdout: {e.stdout}")
                logger.error(f"stderr: {e.stderr}")
                return {"success": False, "error": e.stderr}
            except Exception as e:
                logger.error(f"Unexpected error during Terraform execution: {e}")
                return {"success": False, "error": str(e)}

    @classmethod
    def apply(cls, credentials: dict, config: dict):
        return cls.run_terraform(["apply", "-auto-approve"], credentials, config)

    @classmethod
    def destroy(cls, credentials: dict, config: dict):
        return cls.run_terraform(["destroy", "-auto-approve"], credentials, config)
