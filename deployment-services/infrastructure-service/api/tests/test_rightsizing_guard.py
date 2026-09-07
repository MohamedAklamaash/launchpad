"""Rightsizing must only ever talk to AWS with credentials minted by AssumeRole.

STS credentials are no longer persisted to Infrastructure.metadata, so the credentials
come from authenticate_infrastructure()'s return value. Passing empty/missing creds to
boto3 makes it fall back to the default chain (env vars / IMDS) and operate on the
LAUNCHPAD platform account instead of the customer's — so the guard must skip instead.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _infra(*, metadata=None, cloud_provider="aws", id_="11111111-1111-1111-1111-111111111111"):
    # cloud_provider is stored lowercase (CloudProvider.AWS == "aws"); enforce_rightsizing
    # compares against the enum, so the guard only proceeds for "aws".
    return SimpleNamespace(id=id_, cloud_provider=cloud_provider, metadata=metadata or {})


def _creds(access="AKIA_TEST", secret="SECRET_TEST", token="TOKEN_TEST"):
    return {
        "aws_access_key_id": access,
        "aws_secret_access_key": secret,
        "aws_session_token": token,
    }


@pytest.fixture
def patches():
    # The credential-gating tests exercise the production path, so force non-dev mode;
    # the dev-mode short-circuit is covered separately by test_skips_in_dev_mode.
    with patch(
        "api.common.utils.enforce_rightsizing.boto3"
    ) as mock_boto3, patch(
        "api.common.utils.enforce_rightsizing.authenticate_infrastructure"
    ) as mock_auth, patch(
        "api.common.utils.enforce_rightsizing.InfrastructureRepository"
    ) as mock_repo_cls, patch(
        "api.common.utils.enforce_rightsizing.is_dev_mode", return_value=False
    ) as mock_dev:
        mock_boto3.client.return_value = MagicMock(
            get_ec2_instance_recommendations=MagicMock(return_value={"instanceRecommendations": []})
        )
        mock_auth.return_value = _creds()
        repo_instance = MagicMock()
        mock_repo_cls.return_value = repo_instance
        yield SimpleNamespace(
            boto3=mock_boto3, auth=mock_auth, repo=repo_instance, repo_cls=mock_repo_cls, dev=mock_dev
        )


def test_skips_non_aws_infra(patches):
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.repo.get_all.return_value = [_infra(cloud_provider="azure")]

    enforce_rightsizing()

    assert not patches.boto3.client.called


def test_skips_in_dev_mode():
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    with patch("api.common.utils.enforce_rightsizing.is_dev_mode", return_value=True), patch(
        "api.common.utils.enforce_rightsizing.boto3"
    ) as mock_boto3, patch(
        "api.common.utils.enforce_rightsizing.InfrastructureRepository"
    ) as mock_repo_cls:
        enforce_rightsizing()

    assert not mock_boto3.client.called
    assert not mock_repo_cls.return_value.get_all.called


def test_proceeds_to_boto3_when_assume_role_returns_credentials(patches):
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.repo.get_all.return_value = [_infra(metadata={"aws_region": "us-east-1"})]

    enforce_rightsizing()

    assert patches.boto3.client.called
    services = [call.args[0] for call in patches.boto3.client.call_args_list]
    assert "compute-optimizer" in services
    assert "ec2" in services


def test_credentials_come_from_assume_role_not_metadata(patches):
    """The regression guard: metadata carries decoy credentials that must never be used."""
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.auth.return_value = _creds(access="AKIA_FROM_STS", secret="SECRET_FROM_STS")
    patches.repo.get_all.return_value = [
        _infra(metadata={
            "aws_region": "us-east-1",
            "aws_access_key_id": "AKIA_STALE_FROM_METADATA",
            "aws_secret_access_key": "SECRET_STALE_FROM_METADATA",
        })
    ]

    enforce_rightsizing()

    assert patches.boto3.client.called
    for call in patches.boto3.client.call_args_list:
        assert call.kwargs["aws_access_key_id"] == "AKIA_FROM_STS"
        assert call.kwargs["aws_secret_access_key"] == "SECRET_FROM_STS"


@pytest.mark.parametrize("creds", [
    _creds(access="", secret=""),
    _creds(access="AKIA_TEST", secret=""),
    _creds(access="", secret="SECRET_TEST"),
    {},
])
def test_skips_when_assume_role_returns_unusable_credentials(patches, caplog, creds):
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.auth.return_value = creds
    patches.repo.get_all.return_value = [_infra(metadata={"aws_region": "us-east-1"})]

    with caplog.at_level(logging.WARNING):
        enforce_rightsizing()

    assert not patches.boto3.client.called
    assert any("Skipping rightsizing" in r.message for r in caplog.records)
