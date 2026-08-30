import hashlib
from datetime import datetime, timedelta, timezone

DEFAULT_REGION = "us-west-2"
MOCK_ACCOUNT_ID = "000000000000"
MOCK_CREDENTIAL_TTL = timedelta(hours=12)


def _suffix(infra_id) -> str:
    return hashlib.md5(str(infra_id).encode()).hexdigest()[:8]


def _hex_id(prefix: str, infra_id, salt: str = "") -> str:
    digest = hashlib.md5(f"{infra_id}:{salt}".encode()).hexdigest()
    return f"{prefix}-{digest[:17]}"


def resolve_region(infra) -> str:
    metadata = infra.metadata or {}
    return metadata.get("aws_region") or DEFAULT_REGION


def synthesize_assumed_role_metadata(infra) -> dict:
    suffix = _suffix(infra.id)
    expiration = datetime.now(timezone.utc) + MOCK_CREDENTIAL_TTL
    account_id = infra.code or MOCK_ACCOUNT_ID
    return {
        "aws_access_key_id": f"ASIAMOCK{suffix.upper()}",
        "aws_secret_access_key": f"mock-secret-{suffix}",
        "aws_session_token": f"mock-session-token-{suffix}",
        "assumed_role_arn": (
            f"arn:aws:sts::{account_id}:assumed-role/LaunchpadDeploymentRole/launchpad-{infra.id}"
        ),
        "expiration": expiration.isoformat(),
        "is_mock": True,
    }


_MOCK_ENGINE_PORT = {"postgres": 5432, "mysql": 3306, "redis": 6379, "docdb": 27017}


def synthesize_database_outputs(db, region: str = DEFAULT_REGION, account_id: str = MOCK_ACCOUNT_ID) -> dict:
    """Synthesize the same `db_<id8>_endpoint/_port/_secret_arn` output keys the real
    per-DB terraform module would produce, so dev mode exercises the exact same
    `_save_outputs` parsing path as a real apply."""
    mod = db.module_name()
    suffix = _suffix(db.environment_id)
    port = _MOCK_ENGINE_PORT[db.engine]
    host = f"mock-{db.name}-{suffix}.{db.engine}.local"
    secret_arn = f"arn:aws:secretsmanager:{region}:{account_id}:secret:launchpad/{db.name}-{suffix}"
    return {
        f"{mod}_endpoint": host,
        f"{mod}_port": port,
        f"{mod}_secret_arn": secret_arn,
    }


def synthesize_environment_outputs(infra, region: str) -> dict:
    suffix = _suffix(infra.id)
    account_id = infra.code or MOCK_ACCOUNT_ID
    env_name = f"infra-{str(infra.id)[:8]}-{suffix}"
    return {
        "vpc_id": _hex_id("vpc", infra.id, "vpc"),
        "cluster_arn": f"arn:aws:ecs:{region}:{account_id}:cluster/{env_name}-cluster",
        "alb_arn": (
            f"arn:aws:elasticloadbalancing:{region}:{account_id}:loadbalancer/app/{env_name}-alb/{suffix}"
        ),
        "alb_dns": f"dev-mock-{suffix}.{region}.elb.amazonaws.com",
        "alb_security_group_id": _hex_id("sg", infra.id, "albsg"),
        "target_group_arn": (
            f"arn:aws:elasticloadbalancing:{region}:{account_id}:targetgroup/{env_name}-tg/{suffix}"
        ),
        "ecr_repository_url": f"{account_id}.dkr.ecr.{region}.amazonaws.com/{env_name}",
        "ecs_task_execution_role_arn": (
            f"arn:aws:iam::{account_id}:role/{env_name}-ecs-task-execution"
        ),
    }
