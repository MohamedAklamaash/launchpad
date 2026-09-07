import base64

from botocore.signers import RequestSigner

TOKEN_PREFIX = "k8s-aws-v1."
CLUSTER_ID_HEADER = "x-k8s-aws-id"
# The presigned URL is a bearer credential; 60s matches the window `aws eks get-token`
# signs with — the ~14-minute expirationTimestamp it prints is not the presign window.
TOKEN_EXPIRES_SECONDS = 60


def mint_eks_token(session, cluster_name: str, region: str) -> str:
    sts = session.client("sts", region_name=region, endpoint_url=f"https://sts.{region}.amazonaws.com")
    signer = RequestSigner(
        sts.meta.service_model.service_id,
        region,
        "sts",
        "v4",
        session.get_credentials(),
        session.events,
    )
    # x-k8s-aws-id goes through the signer, never appended after: only a SIGNED cluster-id
    # header binds the token to this cluster instead of any cluster the role can reach.
    request = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        "headers": {CLUSTER_ID_HEADER: cluster_name},
        "context": {},
    }
    presigned_url = signer.generate_presigned_url(
        request,
        region_name=region,
        expires_in=TOKEN_EXPIRES_SECONDS,
        operation_name="",
    )
    encoded = base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip("=")
    return TOKEN_PREFIX + encoded


def decode_presigned_url(token: str) -> str:
    if not token.startswith(TOKEN_PREFIX):
        raise ValueError("Not an EKS bearer token")
    encoded = token[len(TOKEN_PREFIX):]
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding).decode()
