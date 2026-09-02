import ipaddress
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

import boto3
from api.cloud_providers.aws.authenticate import authenticate_infrastructure
from api.common import naming
from api.common.envs.application import app_config
from api.mock.aws_fixtures import (
    resolve_region,
    synthesize_database_outputs,
    synthesize_environment_outputs,
)
from api.models.database import Database
from api.models.environment import Environment
from api.models.infrastructure import Infrastructure
from api.services.eks_bootstrap import (
    EksBootstrapError,
    EksBootstrapTimeout,
    bootstrap_eks_environment,
    phase_marker,
)
from api.services.eks_teardown import cleanup_eks_orphans
from api.services.infrastructure import validate_aws_region, validate_vpc_cidr
from api.validators import validate_database_name
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from shared.aws.app_security_group import get_or_create_app_security_group
from shared.enums.orchestrator import ComputeType
from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)

TF_MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "infra" / "aws"
MAX_RETRIES = 3
MOCK_PROVISION_DELAY_SECONDS = 4
EKS_SUPPORTED_CLUSTER_VERSIONS = {"1.29", "1.30", "1.31"}
DEFAULT_EKS_CLUSTER_VERSION = "1.31"
MIN_PUBLIC_ACCESS_PREFIXLEN = 16
MAX_LOG_CHARS = 256_000

# Live (non-terminal) statuses whose Database row still gets a module block emitted into
# generated config. DELETING/DELETED are excluded on purpose: omitting the block is what
# makes terraform plan the destroy for a DELETING row.
_LIVE_DB_STATUSES_FOR_CONFIG = ['PENDING', 'PROVISIONING', 'ACTIVE', 'ERROR']


class TerraformWorker:
    """Stateless Terraform worker with retry and proper error handling"""
    
    @staticmethod
    def _generate_unique_suffix(infra_id: str) -> str:
        """Generate unique suffix for resource names"""
        return naming.unique_suffix(infra_id)
    
    @staticmethod
    def _ensure_backend(credentials: dict, region: str, account_id: str) -> tuple[str, str]:
        """Ensure S3 backend and DynamoDB lock table exist"""
        bucket = f"launchpad-tf-state-{account_id}-{region}"
        table = f"launchpad-tf-locks-{account_id}-{region}"

        if is_dev_mode(app_config.mode):
            logger.warning("MOCK backend ensure skipped in dev mode (no S3/DynamoDB)")
            return bucket, table

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
                 compute_type: str = ComputeType.ECS_FARGATE, ensure_backend: bool = True) -> dict:
        """Execute terraform with proper logging and cleanup"""
        # A missing credential must never silently fall back to the AWS SDK's ambient
        # chain (IMDS, ~/.aws/credentials) — that chain can resolve to the platform's
        # own AWS identity, which can AssumeRole into every onboarded customer account.
        if not all(credentials.get(k) for k in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token")):
            return {"success": False, "error": "Missing AWS credentials for terraform execution", "logs": ""}

        bucket = f"launchpad-tf-state-{account_id}-{region}"
        table = f"launchpad-tf-locks-{account_id}-{region}"
        if ensure_backend:
            bucket, table = TerraformWorker._ensure_backend(credentials, region, account_id)

        tf_config = TerraformWorker._generate_config(
            env_vars, infra_id, bucket, table, region, compute_type, account_id
        )

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

            # Never pass the worker's own environment through: injected HCL can run a
            # local-exec provisioner, and **os.environ would hand it every platform
            # secret (JWT_SECRET, DB passwords, the platform's own AWS keys).
            env = {
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/tmp"),
            }
            env.update({
                "AWS_ACCESS_KEY_ID": credentials.get("aws_access_key_id", ""),
                "AWS_SECRET_ACCESS_KEY": credentials.get("aws_secret_access_key", ""),
                "AWS_SESSION_TOKEN": credentials.get("aws_session_token", ""),
                "AWS_DEFAULT_REGION": region,
                "AWS_EC2_METADATA_DISABLED": "true",
                "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
                "AWS_CONFIG_FILE": "/dev/null",
                "TF_IN_AUTOMATION": "1",
                "TF_INPUT": "0",
                "TF_PLUGIN_CACHE_DIR": str(plugin_cache_dir),
            })
            
            init_result = subprocess.run(
                ["terraform", "init", "-no-color", "-input=false"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                env=env,
                check=False
            )
            logs.append(f"[INIT]\n{init_result.stdout}\n{init_result.stderr}")
            
            if init_result.returncode != 0:
                return {"success": False, "error": init_result.stderr, "logs": "\n".join(logs)}
            
            result = subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                env=env,
                check=False
            )
            logs.append(f"[COMMAND]\n{result.stdout}\n{result.stderr}")
            
            if result.returncode != 0:
                return {"success": False, "error": result.stderr, "logs": "\n".join(logs)}
            
            return {"success": True, "output": result.stdout, "logs": "\n".join(logs)}
        
        except Exception as e:
            error_msg = f"Terraform execution failed: {e!s}"
            logs.append(f"[ERROR] {error_msg}")
            return {"success": False, "error": error_msg, "logs": "\n".join(logs)}
        
        finally:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)
    
    @staticmethod
    def _ensure_app_security_group(credentials: dict, region: str, vpc_id: str, infra_id: str) -> str:
        """Get-or-create the per-infra Fargate app SG so its id exists before a DB module's
        ingress rule can reference it — for both a brand-new and an already-provisioned env."""
        ec2 = boto3.client(
            "ec2", region_name=region,
            aws_access_key_id=credentials.get("aws_access_key_id"),
            aws_secret_access_key=credentials.get("aws_secret_access_key"),
            aws_session_token=credentials.get("aws_session_token"),
        )
        return get_or_create_app_security_group(ec2, infra_id, vpc_id)

    @staticmethod
    def _db_module_blocks(infra_id: str, env_name: str, app_sg_id: str) -> str:
        """One Terraform module block (+ outputs) per live Database row for this
        environment. A row not included here (DELETING/DELETED) has no module block, so
        terraform plans its destroy on the next apply."""
        live_dbs = TerraformWorker._live_dbs_for_infra(infra_id)

        blocks = []
        for db in live_dbs:
            # Defense-in-depth: re-check at the interpolation sink, not just the create-time
            # API boundary — a row written before this check existed must not still reach here.
            validate_database_name(db.name)
            mod = db.module_name()

            if db.engine in ("postgres", "mysql"):
                blocks.append(f"""
module "{mod}" {{
  source                    = "./modules/rds"
  environment                = "{env_name}"
  engine                     = "{db.engine}"
  engine_version              = "{db.engine_version}"
  instance_class              = "{db.instance_class}"
  allocated_storage           = {db.allocated_storage}
  db_name                     = "{db.name}"
  vpc_id                      = module.vpc.vpc_id
  private_subnet_ids          = module.vpc.private_subnet_ids
  app_security_group_id       = "{app_sg_id}"
  final_snapshot_identifier   = "{db.final_snapshot_id}"

  depends_on = [module.vpc]
}}

output "{mod}_endpoint" {{ value = module.{mod}.endpoint }}
output "{mod}_port" {{ value = module.{mod}.port }}
output "{mod}_secret_arn" {{ value = module.{mod}.secret_arn }}
""")
            elif db.engine == "redis":
                blocks.append(f"""
module "{mod}" {{
  source                    = "./modules/elasticache"
  environment                = "{env_name}"
  engine_version              = "{db.engine_version}"
  node_type                   = "{db.instance_class}"
  db_name                     = "{db.name}"
  vpc_id                      = module.vpc.vpc_id
  private_subnet_ids          = module.vpc.private_subnet_ids
  app_security_group_id       = "{app_sg_id}"

  depends_on = [module.vpc]
}}

output "{mod}_endpoint" {{ value = module.{mod}.endpoint }}
output "{mod}_port" {{ value = module.{mod}.port }}
output "{mod}_secret_arn" {{ value = module.{mod}.secret_arn }}
""")
            elif db.engine == "docdb":
                blocks.append(f"""
module "{mod}" {{
  source                    = "./modules/docdb"
  environment                = "{env_name}"
  engine_version              = "{db.engine_version}"
  instance_class              = "{db.instance_class}"
  allocated_storage           = {db.allocated_storage}
  db_name                     = "{db.name}"
  vpc_id                      = module.vpc.vpc_id
  private_subnet_ids          = module.vpc.private_subnet_ids
  app_security_group_id       = "{app_sg_id}"
  final_snapshot_identifier   = "{db.final_snapshot_id}"

  depends_on = [module.vpc]
}}

output "{mod}_endpoint" {{ value = module.{mod}.endpoint }}
output "{mod}_port" {{ value = module.{mod}.port }}
output "{mod}_secret_arn" {{ value = module.{mod}.secret_arn }}
""")
        return "\n".join(blocks)

    @staticmethod
    def _live_dbs_for_infra(infra_id: str):
        """Live Database rows for infra_id, or empty if infra_id isn't a real UUID (a
        few worker tests exercise `_exec_tf` in isolation with a synthetic id)."""
        try:
            uuid.UUID(str(infra_id))
        except (ValueError, AttributeError, TypeError):
            return Database.objects.none()
        return Database.objects.filter(
            environment__infrastructure_id=infra_id,
            status__in=_LIVE_DB_STATUSES_FOR_CONFIG,
        )

    @staticmethod
    def _db_secret_arn_refs(infra_id: str) -> str:
        """HCL list literal of `module.<db>.secret_arn` references, for the exec-role
        policy's `db_secret_arns` variable. A direct module-output reference (not a
        precomputed ARN string) since RDS/DocDB secret ARNs only exist post-apply."""
        refs = [f"module.{db.module_name()}.secret_arn" for db in TerraformWorker._live_dbs_for_infra(infra_id)]
        return "[" + ", ".join(refs) + "]"

    @staticmethod
    def _generate_config(vars: dict, infra_id: str, bucket: str, table: str, region: str,
                         compute_type: str, account_id: str) -> str:
        if compute_type == ComputeType.EKS:
            return TerraformWorker._generate_config_eks(vars, infra_id, bucket, table, region, account_id)
        return TerraformWorker._generate_config_ecs(vars, infra_id, bucket, table, region)

    @staticmethod
    def _generate_config_ecs(vars: dict, infra_id: str, bucket: str, table: str, region: str) -> str:
        """Generate Terraform config with unique resource names"""
        env_name = naming.environment_name(infra_id)
        db_blocks = TerraformWorker._db_module_blocks(infra_id, env_name, vars.get("db_app_sg_id", ""))
        db_secret_arns = TerraformWorker._db_secret_arn_refs(infra_id)

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
      version = ">=5.13,<6.0"
    }}
    random = {{
      source  = "hashicorp/random"
      version = ">=3.6,<4.0"
    }}
  }}
}}

provider "aws" {{
  region = {json.dumps(str(vars.get('aws_region', 'us-west-2')))}
  
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
  source          = "./modules/iam"
  environment     = "{env_name}"
  db_secret_arns  = {db_secret_arns}
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
output "alb_security_group_id" {{ value = module.vpc.alb_security_group_id }}
{db_blocks}"""

    @staticmethod
    def _generate_config_eks(vars: dict, infra_id: str, bucket: str, table: str, region: str,
                             account_id: str) -> str:
        # Must stay identical to what eks_teardown resolves the cluster by; a divergent
        # copy here makes the orphan reap take its "cluster absent" branch against a
        # cluster that is still live.
        env_name = naming.environment_name(infra_id)

        if not re.fullmatch(r"\d{12}", str(account_id)):
            raise ValueError(f"account_id must be a 12-digit AWS account id, got {account_id!r}")

        cluster_version = str(vars.get("cluster_version", DEFAULT_EKS_CLUSTER_VERSION))
        if cluster_version not in EKS_SUPPORTED_CLUSTER_VERSIONS:
            raise ValueError(
                f"Unsupported EKS cluster_version {cluster_version!r}; "
                f"allowed: {sorted(EKS_SUPPORTED_CLUSTER_VERSIONS)}"
            )
        public_access_cidrs = list(settings.EKS_PUBLIC_ACCESS_CIDRS)
        if not public_access_cidrs:
            raise ValueError("EKS_PUBLIC_ACCESS_CIDRS must be a non-empty list")
        for cidr in public_access_cidrs:
            # A literal "0.0.0.0/0" check is not enough: ["0.0.0.0/1", "128.0.0.0/1"]
            # covers the whole internet, as does IPv6 ::/0. Refuse on prefix width.
            try:
                network = ipaddress.ip_network(str(cidr), strict=True)
            except ValueError as exc:
                raise ValueError(f"EKS_PUBLIC_ACCESS_CIDRS contains an invalid CIDR: {cidr}") from exc
            if network.prefixlen < MIN_PUBLIC_ACCESS_PREFIXLEN:
                raise ValueError(
                    f"EKS_PUBLIC_ACCESS_CIDRS entry {cidr} is too broad "
                    f"(prefix must be /{MIN_PUBLIC_ACCESS_PREFIXLEN} or narrower)"
                )
        provisioner_role_arn = f"arn:aws:iam::{account_id}:role/LaunchpadDeploymentRole"

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
      version = ">= 5.79"
    }}
  }}
}}

provider "aws" {{
  region = {json.dumps(str(vars.get('aws_region', 'us-west-2')))}

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
  source                 = "./modules/vpc"
  environment            = "{env_name}"
  vpc_cidr               = {json.dumps(str(vars.get('vpc_cidr', '10.0.0.0/16')))}
  enable_elb_subnet_tags = true
}}

module "iam" {{
  source      = "./modules/iam"
  environment = "{env_name}"
}}

module "eks" {{
  source               = "./modules/eks"
  environment          = "{env_name}"
  cluster_name         = "{env_name}"
  cluster_version      = "{cluster_version}"
  subnet_ids           = module.vpc.private_subnet_ids
  public_access_cidrs  = {json.dumps(public_access_cidrs)}
  provisioner_role_arn = "{provisioner_role_arn}"

  depends_on = [module.vpc]
}}

module "ecr" {{
  source      = "./modules/ecr"
  environment = "{env_name}"
}}

output "vpc_id" {{ value = module.vpc.vpc_id }}
output "cluster_arn" {{ value = module.eks.cluster_arn }}
output "cluster_name" {{ value = module.eks.cluster_name }}
output "cluster_endpoint" {{ value = module.eks.cluster_endpoint }}
output "ecr_repository_url" {{ value = module.ecr.repository_url }}
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
            "timed out",
            "TooManyRequests",
            "ResourceInUseException"
        ]
        return any(pattern.lower() in error.lower() for pattern in transient_patterns)
    
    @staticmethod
    def _mock_provision(infra_id: str, infra: Infrastructure):
        with transaction.atomic():
            env = Environment.objects.select_for_update().get(infrastructure_id=infra_id)
            is_update = env.first_activated_at is not None
            env.status = "UPDATING" if is_update else "PROVISIONING"
            env.save(update_fields=['status'])

        logger.warning(
            "MOCK provisioning infrastructure in dev mode (no terraform, no AWS)",
            extra={"infra_id": str(infra_id), "is_mock": True},
        )
        time.sleep(MOCK_PROVISION_DELAY_SECONDS)

        region = resolve_region(infra)
        outputs = synthesize_environment_outputs(infra, region, infra.compute_type)
        # Dev mode skips the real EC2 boto3 call for the app SG — synthesize a
        # deterministic id so the DB rows still get a host/port/secret_arn to display.
        live_dbs = list(Database.objects.filter(
            environment_id=env.id, status__in=_LIVE_DB_STATUSES_FOR_CONFIG
        ))
        account_id = infra.code or "mock"
        for db in live_dbs:
            outputs.update(synthesize_database_outputs(db, region=region, account_id=account_id))

        TerraformWorker._save_outputs(
            infra_id,
            {"logs": "[MOCK] synthesized environment outputs", "outputs": outputs},
            tf_vars={}, credentials={}, region=region, account_id=infra.code or "mock",
            compute_type=infra.compute_type,
            mock_outputs=outputs,
        )
        logger.info(f"MOCK infrastructure {infra_id} provisioned successfully")

    @staticmethod
    def provision(infra_id: str, retry_count: int = 0):
        """Provision infrastructure with retry logic"""
        dev_mode = is_dev_mode(app_config.mode)
        try:
            infra = Infrastructure.objects.get(id=infra_id)
            if infra.is_mock and not dev_mode:
                raise ValueError("Refusing to provision a mock infrastructure outside dev mode")
            if dev_mode and not infra.is_mock:
                raise ValueError("Refusing mock provisioning against a real infrastructure")

            if infra.is_mock:
                # ACTIVE no longer short-circuits: a DB create/delete on an already-active
                # mock infra legitimately re-enqueues provision() to reconcile the new
                # module set. The only thing that decides PROVISIONING vs UPDATING is
                # first_activated_at (never current status — see _mock_provision).
                authenticate_infrastructure(infra)
                infra.refresh_from_db()
                TerraformWorker._mock_provision(infra_id, infra)
                return

            with transaction.atomic():
                env = Environment.objects.select_for_update().get(infrastructure_id=infra_id)
                # Keyed on first_activated_at, not current status: a reaped run's status may
                # already read UPDATING/PROVISIONING rather than the ACTIVE it started from,
                # and stomping it to PROVISIONING here would erase the UPDATING marker the
                # rollback gate below depends on.
                is_update = env.first_activated_at is not None
                env.status = "UPDATING" if is_update else "PROVISIONING"
                env.retry_count = retry_count
                env.save(update_fields=['status', 'retry_count'])

            credentials = authenticate_infrastructure(infra)
            infra.refresh_from_db()

            metadata = infra.metadata or {}
            # Re-validate at the interpolation sink, not just the create-time boundary —
            # a row written before this check existed must not still reach _generate_config.
            if metadata.get("aws_region") is not None:
                validate_aws_region(metadata["aws_region"])
            if metadata.get("vpc_cidr") is not None:
                validate_vpc_cidr(metadata["vpc_cidr"])
            region = metadata.get("aws_region", "us-west-2")
            account_id = infra.code or "default"
            compute_type = infra.compute_type

            # A live database needs the per-infra app SG to exist before its ingress rule
            # can reference it. Only touched when there's actually a DB to reconcile — a
            # plain env provision/reprovision with no databases never calls EC2.
            db_app_sg_id = ""
            live_db_count = Database.objects.filter(
                environment_id=env.id, status__in=_LIVE_DB_STATUSES_FOR_CONFIG
            ).count()
            if live_db_count and env.vpc_id:
                db_app_sg_id = TerraformWorker._ensure_app_security_group(
                    credentials, region, env.vpc_id, str(infra_id)
                )

            tf_vars = {
                "environment": f"cli-{infra_id}",
                "owner": str(infra.user_id),
                "project": "launchpad-infra",
                "aws_region": region,
                "vpc_cidr": metadata.get("vpc_cidr", "10.0.0.0/16"),
                "cluster_version": metadata.get("cluster_version", DEFAULT_EKS_CLUSTER_VERSION),
                "db_app_sg_id": db_app_sg_id,
            }

            logger.info(f"Running terraform apply for {infra_id}")
            result = TerraformWorker._exec_tf(
                ["terraform", "apply", "-auto-approve", "-no-color", "-input=false"],
                tf_vars, credentials, str(infra_id), region, account_id, compute_type
            )
            
            if not result["success"]:
                TerraformWorker._handle_provision_failure(infra_id, result, tf_vars, credentials, region, account_id, retry_count, compute_type)
                return

            if compute_type == ComputeType.EKS:
                result["logs"] = result.get("logs", "") + "\n" + phase_marker("apply")

            try:
                TerraformWorker._save_outputs(infra_id, result, tf_vars, credentials, region, account_id, compute_type)
            except EksBootstrapTimeout as e:
                TerraformWorker._handle_provision_failure(
                    infra_id,
                    {"error": str(e), "logs": result.get("logs", "") + "\n" + e.logs},
                    tf_vars, credentials, region, account_id, MAX_RETRIES, compute_type
                )
                return
            except EksBootstrapError as e:
                TerraformWorker._handle_provision_failure(
                    infra_id,
                    {"error": str(e), "logs": result.get("logs", "") + "\n" + e.logs},
                    tf_vars, credentials, region, account_id, retry_count, compute_type
                )
                return
            logger.info(f"Infrastructure {infra_id} provisioned successfully")

        except Exception as e:
            logger.exception(f"Provisioning failed for {infra_id}")
            # An environment that has ever activated must not be reported dead over an
            # error that happened before any destructive step ran (e.g. AssumeRole
            # failing on a reprovision) — restore it instead of flipping to ERROR.
            env = Environment.objects.filter(infrastructure_id=infra_id).first()
            with transaction.atomic():
                if env is not None and env.first_activated_at is not None:
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status="ACTIVE", error_message=f"Update failed: {e!s}"
                    )
                    Database.objects.filter(
                        environment=env, status__in=['PENDING', 'PROVISIONING', 'DELETING']
                    ).update(status='ERROR', error_message=f"Update failed: {e!s}")
                else:
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status="ERROR", error_message=str(e)
                    )
    
    @staticmethod
    def _handle_provision_failure(infra_id, result, tf_vars, credentials, region, account_id, retry_count,
                                  compute_type=ComputeType.ECS_FARGATE):
        """Handle provision failure with retry or rollback"""
        error = result.get("error", "Unknown error")
        logs = result.get("logs", "")
        
        logger.error(f"Terraform apply failed for {infra_id}: {error}")

        # Transient errors get their normal retry regardless of activation state — the
        # retry path never destroys anything, so there is nothing to gate here.
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

        # Permanent failure from here: an environment that has ever gone ACTIVE has live
        # resources serving traffic — the rollback-destroy below would tear them down.
        # Return it to last-known-good instead.
        env = Environment.objects.get(infrastructure_id=infra_id)
        if env.first_activated_at is not None:
            logger.error(f"Provision failed for previously-activated {infra_id}; keeping ACTIVE, skipping destroy")
            with transaction.atomic():
                Environment.objects.filter(infrastructure_id=infra_id).update(
                    status="ACTIVE",
                    logs=((env.logs or "") + "\n[FAILED UPDATE]\n" + logs)[-MAX_LOG_CHARS:],
                    error_message=f"Update failed; environment restored to ACTIVE: {error}",
                )
                # Rows mid-flight in this apply have no confirmed outcome — never leave
                # them silently stuck; ERROR is retryable (create again, or delete works
                # from ERROR too).
                Database.objects.filter(
                    environment=env, status__in=['PENDING', 'PROVISIONING', 'DELETING']
                ).update(status='ERROR', error_message=f"Update failed: {error}")
            return

        logger.error(f"Permanent failure, triggering destroy for {infra_id}")
        if compute_type == ComputeType.EKS:
            try:
                reap_logs = cleanup_eks_orphans(Infrastructure.objects.get(id=infra_id), credentials=credentials)
                if reap_logs:
                    logs += "\n" + reap_logs
            except Exception as e:
                logger.warning(f"EKS orphan reap before rollback destroy failed (non-fatal): {e}")
        destroy_result = TerraformWorker._exec_tf(
            ["terraform", "destroy", "-auto-approve", "-no-color", "-input=false"],
            tf_vars, credentials, str(infra_id), region, account_id, compute_type
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
    def _reconcile_databases(env: Environment, outputs: dict) -> list:
        """Update every non-DELETED Database row for this environment against a
        successful apply's outputs, and return the {id,name,engine,host,port,secret_arn,
        status} list to publish on environment.updated v3. Must run inside the same
        transaction as the environment save that calls it.

        A DELETING row has no module block in the generated config (see
        `_db_module_blocks`), so a successful apply means terraform already destroyed it
        — mark it DELETED. Any other row gets its host/port/secret_arn refreshed from
        this apply's outputs; one apply can satisfy several PENDING rows at once.
        """
        payload = []
        for db in Database.objects.select_for_update().filter(environment=env).exclude(status='DELETED'):
            if db.status == 'DELETING':
                db.status = 'DELETED'
                db.host = None
                db.port = None
                db.save(update_fields=['status', 'host', 'port', 'updated_at'])
                continue

            mod = db.module_name()
            endpoint = outputs.get(f"{mod}_endpoint", {}).get("value")
            if endpoint:
                db.host = endpoint
                port = outputs.get(f"{mod}_port", {}).get("value")
                db.port = int(port) if port is not None else db.port
                db.secret_arn = outputs.get(f"{mod}_secret_arn", {}).get("value")
                db.status = 'ACTIVE'
                db.error_message = None
                db.save(update_fields=['host', 'port', 'secret_arn', 'status', 'error_message', 'updated_at'])
            elif db.status not in ('ACTIVE',):
                # Row was PENDING/PROVISIONING and its module produced no output — the
                # apply succeeded overall but this specific resource didn't come up.
                db.status = 'ERROR'
                db.error_message = 'Apply succeeded but produced no output for this database'
                db.save(update_fields=['status', 'error_message', 'updated_at'])

            payload.append({
                'id': str(db.id), 'name': db.name, 'engine': db.engine,
                'host': db.host, 'port': db.port, 'secret_arn': db.secret_arn,
                'status': db.status,
            })
        return payload

    @staticmethod
    def _save_outputs(infra_id, apply_result, tf_vars, credentials, region, account_id,
                      compute_type=ComputeType.ECS_FARGATE, mock_outputs=None):
        """Get terraform outputs and save to database"""
        if mock_outputs is not None:
            output_result = {
                "success": True,
                "output": json.dumps({k: {"value": v} for k, v in mock_outputs.items()}),
                "logs": "[MOCK OUTPUT] synthesized",
            }
        else:
            output_result = TerraformWorker._exec_tf(
                ["terraform", "output", "-json"],
                tf_vars, credentials, str(infra_id), region, account_id, compute_type
            )

        if output_result["success"]:
            outputs = json.loads(output_result["output"])
            # `terraform output -json` prints sensitive-marked values in cleartext (the
            # human-readable apply output masks them) — persist only the key names.
            combined_logs = (apply_result.get("logs", "") + "\n[OUTPUT] parsed keys: "
                              + ", ".join(sorted(outputs.keys())))[-MAX_LOG_CHARS:]

            alb_dns = outputs.get("alb_dns", {}).get("value")
            if compute_type == ComputeType.EKS and mock_outputs is None:
                bootstrap = bootstrap_eks_environment(
                    Infrastructure.objects.get(id=infra_id),
                    credentials=credentials,
                    region=region,
                    cluster_name=outputs.get("cluster_name", {}).get("value"),
                )
                alb_dns = bootstrap.alb_dns
                combined_logs = (combined_logs + "\n[BOOTSTRAP]\n" + bootstrap.logs)[-MAX_LOG_CHARS:]

            with transaction.atomic():
                env = Environment.objects.get(infrastructure_id=infra_id)
                if compute_type == ComputeType.EKS:
                    env.vpc_id = outputs.get("vpc_id", {}).get("value")
                    env.cluster_arn = outputs.get("cluster_arn", {}).get("value")
                    env.ecr_repository_url = outputs.get("ecr_repository_url", {}).get("value")
                    env.alb_dns = alb_dns
                    env.alb_arn = None
                    env.alb_security_group_id = None
                    env.target_group_arn = None
                    env.ecs_task_execution_role_arn = None
                else:
                    env.vpc_id = outputs.get("vpc_id", {}).get("value")
                    env.cluster_arn = outputs.get("cluster_arn", {}).get("value")
                    env.alb_arn = outputs.get("alb_arn", {}).get("value")
                    env.alb_dns = outputs.get("alb_dns", {}).get("value")
                    env.alb_security_group_id = outputs.get("alb_security_group_id", {}).get("value")
                    env.target_group_arn = outputs.get("target_group_arn", {}).get("value")
                    env.ecr_repository_url = outputs.get("ecr_repository_url", {}).get("value")
                    env.ecs_task_execution_role_arn = outputs.get("ecs_task_execution_role_arn", {}).get("value")
                env.status = "ACTIVE"
                env.error_message = None
                if env.first_activated_at is None:
                    env.first_activated_at = timezone.now()
                env.logs = combined_logs
                env.save()

                infra = Infrastructure.objects.get(id=infra_id)

                databases_payload = TerraformWorker._reconcile_databases(env, outputs)

                from api.messaging.producer.producer import infra_producer
                transaction.on_commit(lambda: infra_producer.publish_infra_created(
                    user_id=infra.user_id,
                    infra_id=infra_id,
                    name=infra.name,
                    cloud_provider=infra.cloud_provider,
                    compute_type=infra.compute_type,
                    max_cpu=infra.max_cpu,
                    max_memory=infra.max_memory,
                    code=infra.code,
                    is_cloud_authenticated=infra.is_cloud_authenticated,
                    is_mock=infra.is_mock,
                    metadata=infra.metadata,
                    correlation_id=None
                ))

                # Publish environment.updated after a short delay so the application-service
                # has time to process and commit the infrastructure.created event first.
                _env_id = env.id
                import re as _re
                _sg_id = env.alb_security_group_id
                if _sg_id and not _re.match(r'^sg-[0-9a-f]{8,17}$', _sg_id):
                    logger.error(f"Invalid ALB SG ID format '{_sg_id}' for infra {infra_id} — skipping publish")
                    _sg_id = None
                _env_kwargs = {
                    "infra_id": infra_id,
                    "environment_id": _env_id,
                    "status": "ACTIVE",
                    "vpc_id": env.vpc_id,
                    "cluster_arn": env.cluster_arn,
                    "alb_arn": env.alb_arn,
                    "alb_dns": env.alb_dns,
                    "alb_security_group_id": _sg_id,
                    "target_group_arn": env.target_group_arn,
                    "ecr_repository_url": env.ecr_repository_url,
                    "ecs_task_execution_role_arn": env.ecs_task_execution_role_arn,
                    "databases": databases_payload,
                }
                def _publish_env_delayed(**kwargs):
                    time.sleep(3)
                    infra_producer.publish_environment_updated(**kwargs)
                transaction.on_commit(lambda: threading.Thread(
                    target=_publish_env_delayed, kwargs=_env_kwargs, daemon=True
                ).start())
        else:
            logger.error(f"Failed to fetch terraform outputs for {infra_id}: {output_result.get('error')}")
            combined_logs = (apply_result.get("logs", "") + "\n[OUTPUT FETCH FAILED]\n"
                              + output_result.get("error", ""))[-MAX_LOG_CHARS:]
            env = Environment.objects.get(infrastructure_id=infra_id)
            if env.first_activated_at is not None:
                # Env was live before this run — a failure to read outputs back must
                # never regress it to ERROR; restore it, don't leave it PROVISIONING.
                with transaction.atomic():
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status="ACTIVE", logs=combined_logs,
                        error_message=f"Apply succeeded but reading outputs failed: {output_result.get('error', 'Unknown error')}",
                    )
                # Any Database row mid-flight in this apply has an unconfirmed outcome —
                # ACTIVE has no reaper coverage, so without a re-enqueue here a row could
                # stay PENDING forever with nothing left to re-drive it. A retried apply
                # against unchanged config is a safe terraform no-op.
                if Database.objects.filter(
                    environment=env, status__in=['PENDING', 'PROVISIONING', 'DELETING']
                ).exists():
                    from api.services.infra_queue import InfraQueue
                    InfraQueue.enqueue_provision(str(infra_id))
            else:
                # First-time provision: apply succeeded but we couldn't confirm it, so
                # first_activated_at stays unstamped. Leave status as PROVISIONING —
                # the reaper re-drives it and a retried apply is a safe no-op.
                with transaction.atomic():
                    Environment.objects.filter(infrastructure_id=infra_id).update(logs=combined_logs)

    @staticmethod
    def _pre_destroy_cleanup(credentials: dict, region: str, infra) -> str:
        """Pre-clean resources that block Terraform destroy."""
        if is_dev_mode(app_config.mode):
            logger.warning("MOCK pre-destroy cleanup skipped in dev mode")
            return ""
        infra_id = str(infra.id)

        eks_reap_logs = ""
        if infra.compute_type == ComputeType.EKS:
            try:
                eks_reap_logs = cleanup_eks_orphans(infra, credentials=credentials)
            except Exception as e:
                logger.warning(f"EKS pre-destroy reap failed (non-fatal): {e}")
        import boto3
        boto_kwargs = {
            "region_name": region,
            "aws_access_key_id": credentials.get("aws_access_key_id"),
            "aws_secret_access_key": credentials.get("aws_secret_access_key"),
            "aws_session_token": credentials.get("aws_session_token"),
        }
        account_id = credentials.get("account_id", "")

        # 1. Force-unlock any stale Terraform state lock in DynamoDB
        try:
            table = f"launchpad-tf-locks-{account_id}-{region}"
            lock_key = f"launchpad-tf-state-{account_id}-{region}/infra/{infra_id}/terraform.tfstate"
            dynamodb = boto3.client("dynamodb", **boto_kwargs)
            dynamodb.delete_item(
                TableName=table,
                Key={"LockID": {"S": lock_key}}
            )
            logger.info(f"Force-unlocked Terraform state lock for {infra_id}")
        except Exception as e:
            logger.warning(f"State lock cleanup (non-fatal): {e}")

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
                        time.sleep(2)
                    except Exception:  # noqa: S110 - best-effort detach, the delete below is the real signal
                        pass
                try:
                    ec2.delete_network_interface(NetworkInterfaceId=eni_id)
                    logger.info(f"Deleted ENI {eni_id}")
                except Exception as e:
                    logger.warning(f"Could not delete ENI {eni_id}: {e}")
        except Exception as e:
            logger.warning(f"ENI pre-clean failed (non-fatal): {e}")

        # 3. Remove inbound rules in OTHER security groups that reference our SGs as source.
        try:
            ec2 = boto3.client("ec2", **boto_kwargs)
            our_sgs = ec2.describe_security_groups(
                Filters=[{"Name": "tag:InfraID", "Values": [str(infra_id)]}]
            )["SecurityGroups"]
            our_sg_ids = {sg["GroupId"] for sg in our_sgs}

            for sg_id in our_sg_ids:
                referencing = ec2.describe_security_groups(
                    Filters=[{"Name": "ip-permission.group-id", "Values": [sg_id]}]
                )["SecurityGroups"]
                for ref_sg in referencing:
                    if ref_sg["GroupId"] in our_sg_ids:
                        continue
                    rules_to_revoke = [
                        perm for perm in ref_sg.get("IpPermissions", [])
                        if any(pair.get("GroupId") == sg_id for pair in perm.get("UserIdGroupPairs", []))
                    ]
                    if rules_to_revoke:
                        ec2.revoke_security_group_ingress(
                            GroupId=ref_sg["GroupId"],
                            IpPermissions=rules_to_revoke,
                        )
                        logger.info(f"Revoked {len(rules_to_revoke)} inbound rule(s) in {ref_sg['GroupId']} referencing {sg_id}")
        except Exception as e:
            logger.warning(f"Cross-SG rule cleanup failed (non-fatal): {e}")

        return eks_reap_logs

    @staticmethod
    def destroy(infra_id: str):
        """Destroy infrastructure"""
        try:
            # Defense-in-depth: the primary guard lives at the service layer
            # (delete_infrastructure, before this job is ever enqueued). Re-checked here
            # so this method is safe even if invoked some other way — must run before the
            # DESTROYING status update and before any AWS-touching cleanup.
            live_dbs = Database.objects.filter(
                environment__infrastructure_id=infra_id
            ).exclude(status='DELETED')
            if live_dbs.exists():
                logger.error(
                    f"Refusing to destroy infra {infra_id}: {live_dbs.count()} live database row(s)"
                )
                with transaction.atomic():
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status="ERROR",
                        error_message=f"Destroy blocked: {live_dbs.count()} database(s) must be deleted first",
                    )
                return

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

            dev_mode = is_dev_mode(app_config.mode)
            if infra.is_mock and not dev_mode:
                raise ValueError("Refusing to destroy a mock infrastructure outside dev mode")
            if dev_mode and not infra.is_mock:
                raise ValueError("Refusing mock destroy against a real infrastructure")
            if infra.is_mock:
                logger.warning(
                    "MOCK destroy in dev mode (no terraform, no AWS)",
                    extra={"infra_id": str(infra_id), "is_mock": True},
                )
                with transaction.atomic():
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status="DESTROYED", logs="[MOCK] destroyed"
                    )
                return

            region = metadata.get("aws_region", "us-west-2")
            account_id = infra.code or "default"

            # Credentials are no longer persisted, so there is no stored fallback to degrade to.
            # Continuing with empty creds would skip the EKS orphan reap and then fail inside
            # terraform anyway; raising leaves the env DESTROYING for the reaper to retry.
            try:
                credentials = authenticate_infrastructure(infra)
                infra.refresh_from_db()
                metadata = infra.metadata or {}
                logger.info(f"Re-authenticated infrastructure {infra_id} for destroy")
            except Exception as e:
                raise RuntimeError(
                    f"Cannot destroy {infra_id}: AssumeRole failed ({type(e).__name__})"
                ) from e

            credentials["account_id"] = account_id

            pre_destroy_logs = TerraformWorker._pre_destroy_cleanup(credentials, region, infra)

            tf_vars = {
                "environment": f"cli-{infra_id}",
                "owner": str(infra.user_id),
                "project": "launchpad-infra",
                "aws_region": region,
                "vpc_cidr": metadata.get("vpc_cidr", "10.0.0.0/16"),
                "cluster_version": metadata.get("cluster_version", DEFAULT_EKS_CLUSTER_VERSION)
            }

            result = TerraformWorker._exec_tf(
                ["terraform", "destroy", "-auto-approve", "-no-color", "-input=false"],
                tf_vars, credentials, str(infra_id), region, account_id, infra.compute_type,
                ensure_backend=False  # bucket already exists from provision
            )

            destroy_logs = (pre_destroy_logs + "\n" if pre_destroy_logs else "") + result.get("logs", "")
            with transaction.atomic():
                if result["success"]:
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status="DESTROYED", logs=destroy_logs
                    )
                    logger.info(f"Infrastructure {infra_id} destroyed")
                else:
                    Environment.objects.filter(infrastructure_id=infra_id).update(
                        status="ERROR",
                        error_message=f"Destroy failed: {result.get('error')}",
                        logs=destroy_logs
                    )
                    logger.error(f"Destroy failed for {infra_id}: {result.get('error')}")
        
        except Exception:
            logger.exception(f"Destroy failed for {infra_id}")
