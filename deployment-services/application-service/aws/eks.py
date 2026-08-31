import boto3

DEPLOY_ROLE_SESSION_SECONDS = 3600


def cluster_name_from_arn(cluster_arn: str) -> str:
    if not cluster_arn or "/" not in cluster_arn:
        raise ValueError(f"Malformed EKS cluster ARN: {cluster_arn!r}")
    return cluster_arn.rsplit("/", 1)[1]


def assume_deploy_role(session, account_id: str, cluster_name: str, region: str):
    """Chain into the namespace-scoped {cluster}-deploy role. The provisioner's cluster-admin
    identity must never be the one that applies customer workloads."""
    response = session.client("sts").assume_role(
        RoleArn=f"arn:aws:iam::{account_id}:role/{cluster_name}-deploy",
        RoleSessionName=f"launchpad-deploy-{cluster_name}"[:64],
        DurationSeconds=DEPLOY_ROLE_SESSION_SECONDS,
    )
    credentials = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )


class EKSClient:
    def __init__(self, session):
        self.client = session.client("eks")

    def describe_cluster(self, cluster_name: str) -> dict:
        """Endpoint + CA are fetched fresh on every deploy and never persisted."""
        cluster = self.client.describe_cluster(name=cluster_name)["cluster"]
        return {
            "endpoint": cluster["endpoint"],
            "ca_data": cluster["certificateAuthority"]["data"],
        }
