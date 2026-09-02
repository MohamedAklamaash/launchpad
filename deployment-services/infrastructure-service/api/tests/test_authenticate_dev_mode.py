"""SEAM 1 + H3 guard tests for authenticate_infrastructure.

Dev mode: writes synthesized fake creds to infra.metadata, marks authenticated,
and never touches boto3. Prod mode: still calls the real STS seam. H3 promotion
guard: mismatched is_mock / mode raises before any AWS work.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from api.cloud_providers.aws import authenticate as auth_mod


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(),
            email=f"u-{uuid.uuid4()}@example.com",
            user_name="tester",
        )

    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(*, is_mock=False, code="123456789012", metadata=None):
        infra = Infrastructure.objects.create(
            user=make_user(),
            name=f"infra-{uuid.uuid4()}",
            cloud_provider="aws",
            max_cpu=1.0,
            max_memory=2.0,
            code=code,
            metadata=metadata,
        )
        if is_mock:
            # is_mock is editable=False; set directly and persist.
            type(infra).objects.filter(id=infra.id).update(is_mock=True)
            infra.refresh_from_db()
        return infra

    return _make


def _force_mode(dev: bool):
    """Patch the is_dev_mode used inside the authenticate module."""
    return patch.object(auth_mod, "is_dev_mode", return_value=dev)


# --- SEAM 1: dev mode mock path -------------------------------------------

def test_dev_mode_synthesizes_creds_and_never_calls_boto3(make_infra):
    infra = make_infra(is_mock=True, metadata={"aws_region": "us-east-1"})

    with _force_mode(True), patch.object(auth_mod, "boto3") as boto3_mock:
        boto3_mock.client.side_effect = AssertionError("boto3.client must not be called in dev mode")
        auth_mod.authenticate_infrastructure(infra)

    infra.refresh_from_db()
    assert infra.is_cloud_authenticated is True
    assert infra.metadata["is_mock"] is True
    assert infra.metadata["aws_access_key_id"].startswith("ASIAMOCK")
    assert "aws_session_token" in infra.metadata


def test_prod_mode_calls_real_sts_assume_role(make_infra):
    import datetime

    infra = make_infra(is_mock=False, metadata={})
    fake_sts = MagicMock()
    fake_sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "AKIAREAL",
            "SecretAccessKey": "realsecret",
            "SessionToken": "realtoken",
            "Expiration": datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc),
        }
    }

    with _force_mode(False), patch.object(auth_mod, "boto3") as boto3_mock:
        boto3_mock.client.return_value = fake_sts
        auth_mod.authenticate_infrastructure(infra)

    boto3_mock.client.assert_called_once()
    fake_sts.assume_role.assert_called_once()
    infra.refresh_from_db()
    assert infra.metadata["aws_access_key_id"] == "AKIAREAL"
    assert infra.is_cloud_authenticated is True


# --- H3 promotion guard ----------------------------------------------------

def test_real_seam_refuses_mock_infra_in_prod(make_infra):
    infra = make_infra(is_mock=True, metadata={})
    with _force_mode(False), patch.object(auth_mod, "boto3") as boto3_mock, \
            pytest.raises(ValueError, match="mock infrastructure"):
        auth_mod.authenticate_infrastructure(infra)
    boto3_mock.client.assert_not_called()


def test_mock_seam_refuses_real_infra_in_dev(make_infra):
    infra = make_infra(is_mock=False, metadata={})
    with _force_mode(True), patch.object(auth_mod, "boto3") as boto3_mock, \
            pytest.raises(ValueError, match="real infrastructure"):
        auth_mod.authenticate_infrastructure(infra)
    boto3_mock.client.assert_not_called()
