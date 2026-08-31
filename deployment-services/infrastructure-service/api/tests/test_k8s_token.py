"""H5 guard tests for shared.k8s.token — the presigned URL is a bearer credential.

The presign window must be 60s (not the ~14-minute expirationTimestamp figure) and
x-k8s-aws-id must be IN X-Amz-SignedHeaders: merely sending it unsigned would let the
token replay as cluster-admin against any cluster the role can reach.
"""
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from shared.k8s.token import (
    CLUSTER_ID_HEADER,
    TOKEN_PREFIX,
    decode_presigned_url,
    mint_eks_token,
)

CLUSTER_NAME = "infra-11111111-abcd1234"
REGION = "us-east-1"


@pytest.fixture
def token():
    session = boto3.Session(
        aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
        aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        aws_session_token="mock-session-token",
        region_name=REGION,
    )
    return mint_eks_token(session, CLUSTER_NAME, REGION)


def test_token_has_prefix_and_unpadded_base64url(token):
    assert token.startswith(TOKEN_PREFIX)
    assert "=" not in token


def test_presigned_url_targets_regional_sts_get_caller_identity(token):
    url = urlparse(decode_presigned_url(token))
    query = parse_qs(url.query)
    assert url.hostname == f"sts.{REGION}.amazonaws.com"
    assert query["Action"] == ["GetCallerIdentity"]


def test_presign_window_is_60_seconds(token):
    query = parse_qs(urlparse(decode_presigned_url(token)).query)
    assert query["X-Amz-Expires"] == ["60"]


def test_cluster_id_header_is_signed(token):
    query = parse_qs(urlparse(decode_presigned_url(token)).query)
    signed_headers = query["X-Amz-SignedHeaders"][0].split(";")
    assert CLUSTER_ID_HEADER in signed_headers


def test_decode_rejects_foreign_strings():
    with pytest.raises(ValueError):
        decode_presigned_url("not-a-token")
