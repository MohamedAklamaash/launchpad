import logging

import boto3
from api.common.envs.application import app_config
from botocore.config import Config
from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)

# Minimal action set actually exercised by a database create apply, per engine. Kept narrow
# so a precheck denial always maps to a real gap in the customer's LaunchpadDeploymentPolicy,
# not an unrelated permission the create path never calls.
_CREATE_ACTIONS_BY_ENGINE = {
    "postgres": ["rds:CreateDBInstance", "rds:CreateDBSubnetGroup", "secretsmanager:CreateSecret"],
    "mysql": ["rds:CreateDBInstance", "rds:CreateDBSubnetGroup", "secretsmanager:CreateSecret"],
    "redis": ["elasticache:CreateReplicationGroup", "elasticache:CreateCacheSubnetGroup", "secretsmanager:CreateSecret"],
    "docdb": ["rds:CreateDBCluster", "rds:CreateDBInstance", "secretsmanager:CreateSecret"],
}


class PolicyRefreshRequired(Exception):
    """Raised when SimulatePrincipalPolicy denies one or more required actions."""

    def __init__(self, denied_actions: list[str]):
        self.denied_actions = denied_actions
        super().__init__(f"Denied actions: {', '.join(denied_actions)}")


def precheck_database_create(infra, engine: str) -> None:
    """Simulate the create actions for `engine` against the assumed role. Raises
    PolicyRefreshRequired on any denial so the caller can return a 422 with the refresh
    snippet instead of letting a real apply fail deep in the worker."""
    if is_dev_mode(app_config.mode):
        logger.warning("MOCK IAM precheck skipped in dev mode", extra={"is_mock": True})
        return

    actions = _CREATE_ACTIONS_BY_ENGINE[engine]
    metadata = infra.metadata or {}
    account_id = infra.code

    iam = boto3.client(
        "iam",
        aws_access_key_id=metadata.get("aws_access_key_id"),
        aws_secret_access_key=metadata.get("aws_secret_access_key"),
        aws_session_token=metadata.get("aws_session_token"),
        config=Config(connect_timeout=5, read_timeout=8, retries={"max_attempts": 2}),
    )

    role_arn = f"arn:aws:iam::{account_id}:role/LaunchpadDeploymentRole"
    response = iam.simulate_principal_policy(PolicySourceArn=role_arn, ActionNames=actions)

    denied = [
        result["EvalActionName"]
        for result in response.get("EvaluationResults", [])
        if result.get("EvalDecision") != "allowed"
    ]
    if denied:
        raise PolicyRefreshRequired(denied)
