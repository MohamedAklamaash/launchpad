import os
import time
import threading
import subprocess
import logging
import boto3
import json
import hashlib
from pathlib import Path
from django.db import transaction
from api.models.infrastructure import Infrastructure
from api.models.environment import Environment
from api.cloud_providers.aws.authenticate import authenticate_infrastructure

logger = logging.getLogger(__name__)

TF_MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "infra" / "aws"
MAX_RETRIES = 3


class TerraformWorker:
    """Stateless Terraform worker with retry and proper error handling"""
    
    @staticmethod
    def _generate_unique_suffix(infra_id: str) -> str:
        """Generate unique suffix for resource names"""
        return hashlib.md5(str(infra_id).encode()).hexdigest()[:8]
    
    @staticmethod
    def _ensure_backend(credentials: dict, region: str, account_id: str) -> tuple[str, str]:
        """Ensure S3 backend and DynamoDB lock table exist"""
        bucket = f"launchpad-tf-state-{account_id}-{region}"
        table = f"launchpad-tf-locks-{account_id}-{region}"
        
        s3 = boto3.client("s3", region_name=region, **credentials)
        dynamodb = boto3.client("dynamodb", region_name=region, **credentials)
        
        try:
            s3.head_bucket(Bucket=bucket)
            logger.info(f"S3 bucket {bucket} exists and is accessible")
        except s3.exceptions.ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code in ['404', '403']:
                try:
                    logger.info(f"Attempting to create S3 bucket: {bucket}")
                    if region == "us-east-1":
                        s3.create_bucket(Bucket=bucket)
                    else:
                        s3.create_bucket(
                            Bucket=bucket,
                            CreateBucketConfiguration={"LocationConstraint": region}
                        )
                    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
                    s3.put_public_access_block(
                        Bucket=bucket,
                        PublicAccessBlockConfiguration={
                            "BlockPublicAcls": True,
                            "IgnorePublicAcls": True,
                            "BlockPublicPolicy": True,
                            "RestrictPublicBuckets": True
                        }
                    )
                    logger.info(f"Successfully created S3 bucket: {bucket}")
                except s3.exceptions.BucketAlreadyExists:
                    logger.info(f"S3 bucket {bucket} already exists globally")
                except s3.exceptions.BucketAlreadyOwnedByYou:
                    logger.info(f"S3 bucket {bucket} already owned by you")
                except Exception as create_error:
                    logger.error(f"Failed to create S3 bucket {bucket}: {create_error}")
                    if "BucketAlreadyExists" not in str(create_error) and "BucketAlreadyOwnedByYou" not in str(create_error):
                        raise
            else:
                raise
        
        try:
            dynamodb.describe_table(TableName=table)
            logger.info(f"DynamoDB table {table} exists")
        except dynamodb.exceptions.ResourceNotFoundException:
            try:
                logger.info(f"Creating DynamoDB table: {table}")
                dynamodb.create_table(
                    TableName=table,
                    KeySchema=[{"AttributeName": "LockID", "KeyType": "HASH"}],
                    AttributeDefinitions=[{"AttributeName": "LockID", "AttributeType": "S"}],
                    BillingMode="PAY_PER_REQUEST"
                )
                logger.info(f"Successfully created DynamoDB table: {table}")
            except Exception as e:
                if "ResourceInUseException" in str(e):
                    logger.info(f"DynamoDB table {table} is already being created or exists")
                else:
                    logger.error(f"Failed to create DynamoDB table: {e}")
                    raise
        
        return bucket, table
    
    @staticmethod
    def _exec_tf(cmd: list, env_vars: dict, credentials: dict, infra_id: str, region: str, account_id: str,
                 ensure_backend: bool = True) -> dict:
        """Execute terraform with proper logging and cleanup"""
        bucket = f"launchpad-tf-state-{account_id}-{region}"
        table = f"launchpad-tf-locks-{account_id}-{region}"
        if ensure_backend:
            bucket, table = TerraformWorker._ensure_backend(credentials, region, account_id)

        unique_suffix = TerraformWorker._generate_unique_suffix(infra_id)
        tf_config = TerraformWorker._generate_config(env_vars, infra_id, bucket, table, region, unique_suffix)

        work_dir = Path(f"/dev/shm/tf-{infra_id}")
        work_dir.mkdir(parents=True, exist_ok=True)

        # Persistent provider cache avoids re-downloading ~200MB provider
        plugin_cache_dir = Path("/tmp/tf-plugin-cache")
        plugin_cache_dir.mkdir(parents=True, exist_ok=True)

        logs = []

        try:
            (work_dir / "main.tf").write_text(tf_config)

            import shutil
            for module in TF_MODULES_DIR.glob("modules/*"):
                if module.is_dir():
                    shutil.copytree(module, work_dir / "modules" / module.name, dirs_exist_ok=True)

            env = {
                **os.environ,
                "AWS_ACCESS_KEY_ID": credentials.get("aws_access_key_id", ""),
                "AWS_SECRET_ACCESS_KEY": credentials.get("aws_secret_access_key", ""),
                "AWS_SESSION_TOKEN": credentials.get("aws_session_token", ""),
                "AWS_DEFAULT_REGION": region,
                "TF_IN_AUTOMATION": "1",
                "TF_INPUT": "0",
                "TF_PLUGIN_CACHE_DIR": str(plugin_cache_dir),
            }
            
            init_result = subprocess.run(
                ["terraform", "init", "-no-color", "-input=false"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                env=env
            )
            logs.append(f"[INIT]\n{init_result.stdout}\n{init_result.stderr}")
            
            if init_result.returncode != 0:
                return {"success": False, "error": init_result.stderr, "logs": "\n".join(logs)}
            
            result = subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                env=env
            )
            logs.append(f"[COMMAND]\n{result.stdout}\n{result.stderr}")
            
            if result.returncode != 0:
                return {"success": False, "error": result.stderr, "logs": "\n".join(logs)}
            
            return {"success": True, "output": result.stdout, "logs": "\n".join(logs)}
        
        except Exception as e:
            error_msg = f"Terraform execution failed: {str(e)}"
            logs.append(f"[ERROR] {error_msg}")
            return {"success": False, "error": error_msg, "logs": "\n".join(logs)}
        
        finally:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)
    
    @staticmethod
    def _generate_config(vars: dict, infra_id: str, bucket: str, table: str, region: str, suffix: str) -> str:
        """Generate Terraform config with unique resource names"""
        env_name = f"infra-{infra_id[:8]}-{suffix}"
        
        return f"""
terraform {{
  backend "s3" {{
    bucket         = "{bucket}"
    key            = "infra/{infra_id}/terraform.tfstate"
    region         = "{region}"
    dynamodb_table = "{table}"
    encrypt        = true
  }}
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
}}

provider "aws" {{
  region = "{vars.get('aws_region', 'us-west-2')}"
  
  default_tags {{
    tags = {{
      Environment   = "{env_name}"
      InfraID       = "{infra_id}"
      ManagedBy     = "launchpad"
      Owner         = "{vars.get('owner', 'unknown')}"
    }}
  }}
}}

module "vpc" {{
  source      = "./modules/vpc"
  environment = "{env_name}"
  vpc_cidr    = "{vars.get('vpc_cidr', '10.0.0.0/16')}"
}}

module "iam" {{
  source      = "./modules/iam"
  environment = "{env_name}"
}}

module "ecs" {{
  source             = "./modules/ecs"
  environment        = "{env_name}"
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  
  depends_on = [module.vpc]
}}

module "alb" {{
  source                 = "./modules/alb"
  environment            = "{env_name}"
  vpc_id                 = module.vpc.vpc_id
  public_subnet_ids      = module.vpc.public_subnet_ids
  alb_security_group_id  = module.vpc.alb_security_group_id
  
  depends_on = [module.vpc]
}}

module "ecr" {{
  source      = "./modules/ecr"
  environment = "{env_name}"
}}

output "vpc_id" {{ value = module.vpc.vpc_id }}
output "cluster_arn" {{ value = module.ecs.cluster_arn }}
output "alb_arn" {{ value = module.alb.alb_arn }}
output "alb_dns" {{ value = module.alb.alb_dns }}
output "target_group_arn" {{ value = module.alb.target_group_arn }}
output "ecr_repository_url" {{ value = module.ecr.repository_url }}
output "ecs_task_execution_role_arn" {{ value = module.iam.ecs_task_execution_role_arn }}
"""
    
    @staticmethod
    def _is_transient_error(error: str) -> bool:
        """Check if error is transient and retryable"""
        transient_patterns = [
            "RequestLimitExceeded",
            "Throttling",
            "ServiceUnavailable",
            "InternalError",
            "connection",
            "timeout",
            "TooManyRequests"
        ]
        return any(pattern.lower() in error.lower() for pattern in transient_patterns)
    
    @staticmethod
    def provision(infra_id: str, retry_count: int = 0):
        """Provision infrastructure with retry logic"""
        try:
            with transaction.atomic():
                env = Environment.objects.select_for_update().get(infrastructure_id=infra_id)
                if env.status == "ACTIVE":
                    logger.warning(f"Infrastructure {infra_id} already active")
                    return
                env.status = "PROVISIONING"
                env.retry_count = retry_count
                env.save(update_fields=['status', 'retry_count'])
            
            infra = Infrastructure.objects.get(id=infra_id)
            authenticate_infrastructure(infra)
            infra.refresh_from_db()
            
            metadata = infra.metadata or {}
            region = metadata.get("aws_region", "us-west-2")
            account_id = infra.code or "default"
            
            credentials = {
                "aws_access_key_id": metadata.get("aws_access_key_id", ""),
                "aws_secret_access_key": metadata.get("aws_secret_access_key", ""),
                "aws_session_token": metadata.get("aws_session_token", "")
            }
            
            tf_vars = {
                "environment": f"cli-{infra_id}",
                "owner": str(infra.user_id),
                "project": "launchpad-infra",
                "aws_region": region,
                "vpc_cidr": metadata.get("vpc_cidr", "10.0.0.0/16")
            }
            
            logger.info(f"Running terraform apply for {infra_id}")
            result = TerraformWorker._exec_tf(
                ["terraform", "apply", "-auto-approve", "-no-color", "-input=false"],
                tf_vars, credentials, str(infra_id), region, account_id
            )
            
            if not result["success"]:
                TerraformWorker._handle_provision_failure(infra_id, result, tf_vars, credentials, region, account_id, retry_count)
                return
            
            TerraformWorker._save_outputs(infra_id, result, tf_vars, credentials, region, account_id)
            logger.info(f"Infrastructure {infra_id} provisioned successfully")
        
        except Exception as e:
            logger.error(f"Provisioning failed for {infra_id}: {str(e)}", exc_info=True)
            with transaction.atomic():
                Environment.objects.filter(infrastructure_id=infra_id).update(
                    status="ERROR", error_message=str(e)
                )
    
    @staticmethod
    def _handle_provision_failure(infra_id, result, tf_vars, credentials, region, account_id, retry_count):
        """Handle provision failure with retry or rollback"""
        error = result.get("error", "Unknown error")
        logs = result.get("logs", "")
        
        logger.error(f"Terraform apply failed for {infra_id}: {error}")
        
        if TerraformWorker._is_transient_error(error) and retry_count < MAX_RETRIES:
            logger.warning(f"Transient error, will retry (attempt {retry_count + 1}/{MAX_RETRIES})")
            with transaction.atomic():
                Environment.objects.filter(infrastructure_id=infra_id).update(
                    logs=logs, error_message=f"Retry {retry_count + 1}: {error}"
                )
            from api.services.infra_queue import InfraQueue
            InfraQueue.release_lock(str(infra_id))
            InfraQueue.enqueue_provision(str(infra_id))
            return
        
        logger.error(f"Permanent failure, triggering destroy for {infra_id}")
        destroy_result = TerraformWorker._exec_tf(
            ["terraform", "destroy", "-auto-approve", "-no-color", "-input=false"],
            tf_vars, credentials, str(infra_id), region, account_id
        )
        
        if destroy_result["success"]:
            logger.info(f"Successfully destroyed resources for failed infrastructure {infra_id}")
            cleanup_status = "All resources were destroyed."
        else:
            logger.error(f"Failed to destroy resources for {infra_id}: {destroy_result.get('error')}")
            cleanup_status = f"WARNING: Cleanup failed. Manual cleanup required in AWS account. Error: {destroy_result.get('error', 'Unknown')}"
        
        combined_logs = logs + "\n[DESTROY]\n" + destroy_result.get("logs", "")
        with transaction.atomic():
            Environment.objects.filter(infrastructure_id=infra_id).update(
                status="ERROR", logs=combined_logs, error_message=f"{error}\n\nCleanup: {cleanup_status}"
            )
    
    @staticmethod
    def _save_outputs(infra_id, apply_result, tf_vars, credentials, region, account_id):
        """Get terraform outputs and save to database"""
        output_result = TerraformWorker._exec_tf(
            ["terraform", "output", "-json"],
            tf_vars, credentials, str(infra_id), region, account_id
        )
        
        if output_result["success"]:
            outputs = json.loads(output_result["output"])
            combined_logs = apply_result.get("logs", "") + "\n[OUTPUT]\n" + output_result.get("logs", "")
            
            with transaction.atomic():
                env = Environment.objects.get(infrastructure_id=infra_id)
                env.vpc_id = outputs.get("vpc_id", {}).get("value")
                env.cluster_arn = outputs.get("cluster_arn", {}).get("value")
                env.alb_arn = outputs.get("alb_arn", {}).get("value")
                env.alb_dns = outputs.get("alb_dns", {}).get("value")
                env.target_group_arn = outputs.get("target_group_arn", {}).get("value")
                env.ecr_repository_url = outputs.get("ecr_repository_url", {}).get("value")
                env.ecs_task_execution_role_arn = outputs.get("ecs_task_execution_role_arn", {}).get("value")
                env.status = "ACTIVE"
                env.logs = combined_logs
                env.save()
                
                infra = Infrastructure.objects.get(id=infra_id)
                
                from api.messaging.producer.producer import infra_producer
                transaction.on_commit(lambda: infra_producer.publish_infra_created(
                    user_id=infra.user_id,
                    infra_id=infra_id,
                    name=infra.name,
                    cloud_provider=infra.cloud_provider,
                    max_cpu=infra.max_cpu,
                    max_memory=infra.max_memory,
                    code=infra.code,
                    is_cloud_authenticated=infra.is_cloud_authenticated,
                    metadata=infra.metadata,
                    correlation_id=None
                ))

                # Publish environment.updated after a short delay so the application-service
                # has time to process and commit the infrastructure.created event first.
                _env_id = env.id
                _env_kwargs = dict(
                    infra_id=infra_id,
                    environment_id=_env_id,
                    status="ACTIVE",
                    vpc_id=env.vpc_id,
                    cluster_arn=env.cluster_arn,
                    alb_arn=env.alb_arn,
                    alb_dns=env.alb_dns,
                    target_group_arn=env.target_group_arn,
                    ecr_repository_url=env.ecr_repository_url,
                    ecs_task_execution_role_arn=env.ecs_task_execution_role_arn,
                )
                def _publish_env_delayed(**kwargs):
                    time.sleep(3)
                    infra_producer.publish_environment_updated(**kwargs)
                transaction.on_commit(lambda: threading.Thread(
                    target=_publish_env_delayed, kwargs=_env_kwargs, daemon=True
                ).start())
    
    @staticmethod
    def _pre_destroy_cleanup(credentials: dict, region: str, infra_id: str):
        """Pre-clean resources that block Terraform destroy:
        - Detach/delete ENIs attached to ECS task security groups (causes SG DependencyViolation)
        - Force-delete all ECR images (ECR repo must be empty)
        """
        import boto3
        boto_kwargs = {
            "region_name": region,
            "aws_access_key_id": credentials.get("aws_access_key_id"),
            "aws_secret_access_key": credentials.get("aws_secret_access_key"),
            "aws_session_token": credentials.get("aws_session_token"),
        }
        suffix = TerraformWorker._generate_unique_suffix(infra_id)
        env_name = f"infra-{infra_id[:8]}-{suffix}"

        # 1. Force-delete all images in the ECR repo
        try:
            ecr = boto3.client("ecr", **boto_kwargs)
            repo_name = f"{env_name}-repo"
            paginator = ecr.get_paginator("list_images")
            image_ids = []
            for page in paginator.paginate(repositoryName=repo_name):
                image_ids.extend(page.get("imageIds", []))
            if image_ids:
                ecr.batch_delete_image(repositoryName=repo_name, imageIds=image_ids)
                logger.info(f"Deleted {len(image_ids)} ECR images from {repo_name}")
        except Exception as e:
            logger.warning(f"ECR pre-clean failed (non-fatal): {e}")

        # 2. Delete any lingering ENIs in the VPC tagged to this infra (unblocks SG deletion)
        try:
            ec2 = boto3.client("ec2", **boto_kwargs)
            enis = ec2.describe_network_interfaces(
                Filters=[{"Name": "tag:InfraID", "Values": [str(infra_id)]}]
            )["NetworkInterfaces"]
            for eni in enis:
                eni_id = eni["NetworkInterfaceId"]
                attachment = eni.get("Attachment", {})
                if attachment.get("AttachmentId") and attachment.get("Status") != "detached":
                    try:
                        ec2.detach_network_interface(AttachmentId=attachment["AttachmentId"], Force=True)
                        import time; time.sleep(2)
                    except Exception:
                        pass
                try:
                    ec2.delete_network_interface(NetworkInterfaceId=eni_id)
                    logger.info(f"Deleted ENI {eni_id}")
                except Exception as e:
                    logger.warning(f"Could not delete ENI {eni_id}: {e}")
        except Exception as e:
            logger.warning(f"ENI pre-clean failed (non-fatal): {e}")

    @staticmethod
    def destroy(infra_id: str):
        """Destroy infrastructure"""
        try:
            with transaction.atomic():
                Environment.objects.filter(infrastructure_id=infra_id).update(status="DESTROYING")
            
            try:
                infra = Infrastructure.objects.get(id=infra_id)
                metadata = infra.metadata or {}
            except Infrastructure.DoesNotExist:
                logger.warning(f"Infrastructure {infra_id} already deleted, skipping destroy")
                with transaction.atomic():
                    Environment.objects.filter(infrastructure_id=infra_id).update(status="DESTROYED")
                return
            
            region = metadata.get("aws_region", "us-west-2")
            account_id = infra.code or "default"

            try:
                authenticate_infrastructure(infra)
                infra.refresh_from_db()
                metadata = infra.metadata or {}
                logger.info(f"Re-authenticated infrastructure {infra_id} for destroy")
            except Exception as e:
                logger.warning(f"Re-auth failed for {infra_id}: {e} — proceeding with stored credentials")

            credentials = {
                "aws_access_key_id": metadata.get("aws_access_key_id", ""),
                "aws_secret_access_key": metadata.get("aws_secret_access_key", ""),
                "aws_session_token": metadata.get("aws_session_token", "")
            }

            TerraformWorker._pre_destroy_cleanup(credentials, region, infra_id)

            tf_vars = {
                "environment": f"cli-{infra_id}",
                "owner": str(infra.user_id),
                "project": "launchpad-infra",
                "aws_region": region,
                "vpc_cidr": metadata.get("vpc_cidr", "10.0.0.0/16")
            }

            result = TerraformWorker._exec_tf(
                ["terraform", "destroy", "-auto-approve", "-no-color", "-input=false"],
                tf_vars, credentials, str(infra_id), region, account_id,
                ensure_backend=False  # bucket already exists from provision
            )
            
            with transaction.atomic():
                if result["success"]:
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status="DESTROYED", logs=result.get("logs", "")
                    )
                    logger.info(f"Infrastructure {infra_id} destroyed")
                else:
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status="ERROR",
                        error_message=f"Destroy failed: {result.get('error')}",
                        logs=result.get("logs", "")
                    )
                    logger.error(f"Destroy failed for {infra_id}: {result.get('error')}")
        
        except Exception as e:
            logger.error(f"Destroy failed for {infra_id}: {str(e)}", exc_info=True)
