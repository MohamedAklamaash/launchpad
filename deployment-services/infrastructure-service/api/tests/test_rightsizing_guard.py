import logging
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest


def _infra(*, metadata, cloud_provider="AWS", id_="11111111-1111-1111-1111-111111111111"):
    # `enforce_rightsizing` filters with `infra.cloud_provider != "AWS"` (string,
    # not the lowercased enum value), so the tests have to feed it "AWS" to
    # exercise the guard. See bug note in the report.
    return SimpleNamespace(id=id_, cloud_provider=cloud_provider, metadata=metadata)


@pytest.fixture
def patches():
    with patch(
        "api.common.utils.enforce_rightsizing.boto3"
    ) as mock_boto3, patch(
        "api.common.utils.enforce_rightsizing.authenticate_infrastructure"
    ) as mock_auth, patch(
        "api.common.utils.enforce_rightsizing.InfrastructureRepository"
    ) as mock_repo_cls:
        mock_boto3.client.return_value = MagicMock(
            get_ec2_instance_recommendations=MagicMock(return_value={"instanceRecommendations": []})
        )
        mock_auth.return_value = None
        repo_instance = MagicMock()
        mock_repo_cls.return_value = repo_instance
        yield SimpleNamespace(
            boto3=mock_boto3, auth=mock_auth, repo=repo_instance, repo_cls=mock_repo_cls
        )


def test_proceeds_to_boto3_when_both_credentials_are_present(patches):
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.repo.get_all.return_value = [
        _infra(metadata={
            "aws_access_key_id": "AKIA_TEST",
            "aws_secret_access_key": "SECRET_TEST",
            "aws_region": "us-east-1",
        })
    ]

    enforce_rightsizing()

    assert patches.boto3.client.called
    services = [call.args[0] for call in patches.boto3.client.call_args_list]
    assert "compute-optimizer" in services
    assert "ec2" in services


def test_skips_when_both_credentials_are_empty_strings(patches, caplog):
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.repo.get_all.return_value = [
        _infra(metadata={"aws_access_key_id": "", "aws_secret_access_key": ""})
    ]

    with caplog.at_level(logging.WARNING):
        enforce_rightsizing()

    assert not patches.boto3.client.called
    assert any("Skipping rightsizing" in r.message for r in caplog.records)


def test_skips_when_metadata_is_missing_credential_keys_entirely(patches, caplog):
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.repo.get_all.return_value = [_infra(metadata={"aws_region": "us-east-1"})]

    with caplog.at_level(logging.WARNING):
        enforce_rightsizing()

    assert not patches.boto3.client.called
    assert any("Skipping rightsizing" in r.message for r in caplog.records)


def test_skips_when_only_access_key_present_and_secret_empty(patches):
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.repo.get_all.return_value = [
        _infra(metadata={
            "aws_access_key_id": "AKIA_TEST",
            "aws_secret_access_key": "",
        })
    ]

    enforce_rightsizing()

    assert not patches.boto3.client.called


def test_skips_when_only_secret_present_and_access_key_empty(patches):
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.repo.get_all.return_value = [
        _infra(metadata={
            "aws_access_key_id": "",
            "aws_secret_access_key": "SECRET_TEST",
        })
    ]

    enforce_rightsizing()

    assert not patches.boto3.client.called


def test_skips_when_metadata_is_none(patches):
    from api.common.utils.enforce_rightsizing import enforce_rightsizing

    patches.repo.get_all.return_value = [_infra(metadata=None)]

    enforce_rightsizing()

    assert not patches.boto3.client.called
