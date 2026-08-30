import hashlib
import logging

logger = logging.getLogger(__name__)


def app_security_group_name(infra_id) -> str:
    """Deterministic per-infra Fargate app SG name, shared by every app in the infra."""
    infra_id = str(infra_id)
    suffix = hashlib.md5(infra_id.encode()).hexdigest()[:8]
    return f"infra-{infra_id[:8]}-{suffix}-fargate-sg"


def get_or_create_app_security_group(ec2_client, infra_id, vpc_id: str) -> str:
    """Get-or-create the per-infra Fargate app security group (no ingress rules).

    Only creates the group so its id exists for callers to reference — e.g. a database
    module's ingress rule, or application-service's own ALB-ingress authorization.
    Name derivation matches application-service's app-SG creator exactly, so whichever
    side runs first the other finds and reuses the same group instead of creating a
    second one.
    """
    sg_name = app_security_group_name(infra_id)

    existing = ec2_client.describe_security_groups(
        Filters=[
            {'Name': 'vpc-id', 'Values': [vpc_id]},
            {'Name': 'group-name', 'Values': [sg_name]},
        ]
    )['SecurityGroups']

    if existing:
        sg_id = existing[0]['GroupId']
        logger.info(f"Reusing existing app security group {sg_name} ({sg_id})")
        return sg_id

    sg_id = ec2_client.create_security_group(
        GroupName=sg_name,
        Description=f"Fargate app security group for infra {infra_id}",
        VpcId=vpc_id,
    )['GroupId']
    logger.info(f"Created app security group {sg_name} ({sg_id})")
    return sg_id
