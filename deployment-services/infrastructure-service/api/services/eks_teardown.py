import logging
import time

import boto3
from api.cloud_providers.aws.authenticate import authenticate_infrastructure
from api.common.envs.application import app_config
from api.common.naming import environment_name
from api.models.environment import Environment
from api.services.eks_bootstrap import BOOTSTRAP_NAMESPACE, phase_marker
from kubernetes import client as k8s
from kubernetes.client.rest import ApiException
from shared.enums.orchestrator import ComputeType
from shared.k8s.client import k8s_api_client
from shared.k8s.token import mint_eks_token
from shared.mode import is_dev_mode

logger = logging.getLogger(__name__)

CLUSTER_TAG_KEY = "elbv2.k8s.aws/cluster"
# EKS Auto Mode's built-in load balancer controller tags its ALBs and security groups
# with these instead (real AWS, 2026-10-16) — matching only CLUSTER_TAG_KEY meant the
# reaper never found an Auto Mode orphan. The cluster key alone is also on other
# EKS-managed resources, so an Auto Mode match additionally requires a controller
# ownership tag.
AUTO_MODE_CLUSTER_TAG_KEY = "eks:eks-cluster-name"
AUTO_MODE_CONTROLLER_TAG_PREFIXES = ("ingress.eks.amazonaws.com/", "service.eks.amazonaws.com/")
APP_NAMESPACE_PREFIX = "app-"
GONE_CLUSTER_STATUSES = {"DELETED", "FAILED"}
ELB_REAP_TIMEOUT_SECONDS = 480
ELB_REAP_POLL_INTERVAL_SECONDS = 20
MAX_ORPHANS_PER_BATCH = 5


def cleanup_eks_orphans(infra, credentials: dict | None = None, bypass_cap: bool = False) -> str:
    """Reap controller-created ALBs/SGs that live outside terraform state.

    Must run before terraform destroy on every teardown entry point: the destroy
    dispatch, the inline rollback after a permanent provision failure, and
    InfrastructureService.delete_infrastructure's no-terraform path.

    `bypass_cap` lifts MAX_ORPHANS_PER_BATCH — set only by a Nuke infrastructure run,
    which intends to remove everything this cluster owns and must not stop partway
    through and call it done. Still scoped to resources tagged/owned by THIS cluster
    (the cluster-name tag plus a terraform-owned VpcId match, same as the capped path) —
    lifting the cap widens how many matching resources get deleted, never which ones.
    """
    if getattr(infra, "compute_type", None) != ComputeType.EKS:
        return ""
    if is_dev_mode(app_config.mode) or getattr(infra, "is_mock", False):
        logger.warning("MOCK EKS orphan cleanup skipped in dev mode", extra={"infra_id": str(infra.id)})
        return ""

    lines = [phase_marker("k8s-reap")]
    credentials = credentials or authenticate_infrastructure(infra)
    region = (infra.metadata or {}).get("aws_region", "us-west-2")
    cluster_name = environment_name(infra.id)
    session = boto3.Session(
        aws_access_key_id=credentials.get("aws_access_key_id"),
        aws_secret_access_key=credentials.get("aws_secret_access_key"),
        aws_session_token=credentials.get("aws_session_token"),
        region_name=region,
    )
    vpc_id = (
        Environment.objects.filter(infrastructure_id=infra.id)
        .values_list("vpc_id", flat=True)
        .first()
    )

    cluster = _describe_cluster(session, cluster_name)
    if cluster is not None and cluster.get("status") not in GONE_CLUSTER_STATUSES:
        try:
            _delete_load_balancer_sources(infra, session, cluster, cluster_name, region, lines)
            _wait_until_load_balancers_reaped(session, cluster_name, vpc_id, lines)
        except Exception as e:
            # A k8s API failure is exactly what a transient network blip produces —
            # it is never a deletion signal (H1), so log and leave AWS untouched.
            lines.append(f"[k8s-reap] in-cluster cleanup failed (non-fatal): {type(e).__name__}: {e}")
            logger.warning(f"EKS in-cluster reap failed for {infra.id} (non-fatal): {e}")
    else:
        status = "absent" if cluster is None else cluster.get("status")
        lines.append(f"[k8s-reap] cluster {status}; reaping orphaned load balancers directly")
        _reap_orphaned_load_balancers(session, cluster_name, vpc_id, lines, bypass_cap=bypass_cap)
        _reap_orphaned_security_groups(session, cluster_name, vpc_id, lines, bypass_cap=bypass_cap)

    return "\n".join(lines)


def _describe_cluster(session, cluster_name: str):
    eks = session.client("eks")
    try:
        return eks.describe_cluster(name=cluster_name)["cluster"]
    except eks.exceptions.ResourceNotFoundException:
        return None


def _delete_load_balancer_sources(infra, session, cluster: dict, cluster_name: str, region: str, lines: list):
    with k8s_api_client(
        infra,
        app_config.mode,
        endpoint=cluster["endpoint"],
        ca_data=cluster["certificateAuthority"]["data"],
        token=mint_eks_token(session, cluster_name, region),
        token_provider=lambda: mint_eks_token(session, cluster_name, region),
    ) as api:
        core = k8s.CoreV1Api(api)
        networking = k8s.NetworkingV1Api(api)
        all_namespaces = [item.metadata.name for item in core.list_namespace().items]
        launchpad_namespaces = [
            name for name in all_namespaces
            if name == BOOTSTRAP_NAMESPACE or name.startswith(APP_NAMESPACE_PREFIX)
        ]
        for namespace in launchpad_namespaces:
            for ingress in networking.list_namespaced_ingress(namespace).items:
                _delete_ignoring_absent(networking.delete_namespaced_ingress, ingress.metadata.name, namespace)
                lines.append(f"[k8s-reap] deleted Ingress {namespace}/{ingress.metadata.name}")
            for service in core.list_namespaced_service(namespace).items:
                if service.spec.type == "LoadBalancer":
                    _delete_ignoring_absent(core.delete_namespaced_service, service.metadata.name, namespace)
                    lines.append(f"[k8s-reap] deleted LoadBalancer Service {namespace}/{service.metadata.name}")


def _delete_ignoring_absent(delete, *args):
    try:
        delete(*args)
    except ApiException as e:
        if e.status != 404:
            raise


def _wait_until_load_balancers_reaped(session, cluster_name: str, vpc_id: str | None, lines: list):
    if not vpc_id:
        lines.append("[k8s-reap] no recorded vpc_id; skipping ELBv2 reap wait")
        return
    elbv2 = session.client("elbv2")
    deadline = time.monotonic() + ELB_REAP_TIMEOUT_SECONDS
    remaining = _matching_load_balancers(elbv2, cluster_name, vpc_id)
    while time.monotonic() < deadline:
        if not remaining:
            lines.append("[k8s-reap] controller load balancers reaped")
            return
        time.sleep(ELB_REAP_POLL_INTERVAL_SECONDS)
        remaining = _matching_load_balancers(elbv2, cluster_name, vpc_id)
    lines.append(
        f"[k8s-reap] {len(remaining)} load balancer(s) still present after {ELB_REAP_TIMEOUT_SECONDS}s; "
        "continuing to terraform destroy"
    )


def _owned_by_cluster_controller(tags: list, cluster_name: str) -> bool:
    """True for a resource the cluster's load balancer controller created — the
    self-managed controller (elbv2.k8s.aws/cluster) or EKS Auto Mode's built-in one."""
    by_key = {t["Key"]: t["Value"] for t in tags}
    if by_key.get(CLUSTER_TAG_KEY) == cluster_name:
        return True
    return by_key.get(AUTO_MODE_CLUSTER_TAG_KEY) == cluster_name and any(
        key.startswith(AUTO_MODE_CONTROLLER_TAG_PREFIXES) for key in by_key
    )


def _matching_load_balancers(elbv2, cluster_name: str, vpc_id: str) -> list[dict]:
    load_balancers = []
    for page in elbv2.get_paginator("describe_load_balancers").paginate():
        load_balancers.extend(lb for lb in page["LoadBalancers"] if lb.get("VpcId") == vpc_id)

    matches = []
    for start in range(0, len(load_balancers), 20):
        batch = load_balancers[start:start + 20]
        tag_descriptions = elbv2.describe_tags(
            ResourceArns=[lb["LoadBalancerArn"] for lb in batch]
        )["TagDescriptions"]
        tagged_arns = {
            description["ResourceArn"]
            for description in tag_descriptions
            if _owned_by_cluster_controller(description["Tags"], cluster_name)
        }
        matches.extend(lb for lb in batch if lb["LoadBalancerArn"] in tagged_arns)
    return matches


def _reap_orphaned_load_balancers(session, cluster_name: str, vpc_id: str | None, lines: list,
                                   bypass_cap: bool = False):
    if not vpc_id:
        lines.append("[k8s-reap] no recorded vpc_id; refusing orphan load balancer reap")
        return
    elbv2 = session.client("elbv2")
    # The cluster tag is forgeable (any principal in the account can set it via
    # alb.ingress.kubernetes.io/tags), so a terraform-owned VpcId match is required too.
    matches = _matching_load_balancers(elbv2, cluster_name, vpc_id)
    if not matches:
        lines.append("[k8s-reap] no orphaned load balancers found")
        return
    if len(matches) > MAX_ORPHANS_PER_BATCH and not bypass_cap:
        lines.append(
            f"[k8s-reap] REFUSING to delete {len(matches)} load balancers (limit {MAX_ORPHANS_PER_BATCH}); "
            "manual cleanup required"
        )
        logger.error(f"EKS orphan reap refused: {len(matches)} LBs matched for {cluster_name}")
        return
    arns = [lb["LoadBalancerArn"] for lb in matches]
    lines.append(f"[k8s-reap] deleting orphaned load balancers: {arns}")
    logger.info(f"Deleting orphaned EKS load balancers for {cluster_name}: {arns}")
    for arn in arns:
        elbv2.delete_load_balancer(LoadBalancerArn=arn)


def _reap_orphaned_security_groups(session, cluster_name: str, vpc_id: str | None, lines: list,
                                    bypass_cap: bool = False):
    if not vpc_id:
        return
    ec2 = session.client("ec2")
    security_groups = [
        group
        for page in ec2.get_paginator("describe_security_groups").paginate(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )
        for group in page["SecurityGroups"]
        if _owned_by_cluster_controller(group.get("Tags", []), cluster_name)
    ]
    if len(security_groups) > MAX_ORPHANS_PER_BATCH and not bypass_cap:
        lines.append(
            f"[k8s-reap] REFUSING to delete {len(security_groups)} security groups "
            f"(limit {MAX_ORPHANS_PER_BATCH}); manual cleanup required"
        )
        return
    for group in security_groups:
        group_id = group["GroupId"]
        attached = ec2.describe_network_interfaces(
            Filters=[{"Name": "group-id", "Values": [group_id]}]
        )["NetworkInterfaces"]
        if attached:
            lines.append(f"[k8s-reap] skipping SG {group_id}: {len(attached)} ENI(s) still attached")
            continue
        try:
            ec2.delete_security_group(GroupId=group_id)
            lines.append(f"[k8s-reap] deleted orphaned security group {group_id}")
        except Exception as e:
            lines.append(f"[k8s-reap] could not delete SG {group_id} (non-fatal): {e}")
