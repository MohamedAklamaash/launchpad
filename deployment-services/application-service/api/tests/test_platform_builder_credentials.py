"""F7 ticket 3 — scoped ECR push-credential minting, isolated from the deploy pipeline.

Covers: exact session-policy shape (actions + single-repository Resource ARN),
DurationSeconds=900, ExternalId=infra.id, that only {registry, username, password,
expires_at} ever leaves the module (never the STS triple), mock-vs-real MODE mismatch
refusal, exited-infra refusal, foreign/malformed repository_name rejection, AccessDenied
-> PolicyRefreshRequiredError, docker_config_json shape, and password redaction in
logs/reprs.
"""
import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import aws.platform_builder_credentials as pbc_mod
import aws.session as aws_session_mod
import pytest
from aws.platform_builder_credentials import (
    InvalidPushRepositoryError,
    PushCredential,
    mint_push_credential,
)
from botocore.exceptions import ClientError

from api.services.exit_enforcement import InfrastructureExitedError
from api.services.policy_errors import PolicyRefreshRequiredError

ACCOUNT_ID = "123456789012"
REGION = "us-west-2"
REPO_NAME = "launchpad-my-app-abcdef12"


def _fake_secret(label: str) -> str:
    """Fixture values are generated, not literals, so secret scanners don't flag test data."""
    return f"fake-{label}-{uuid.uuid4().hex}"


_FAKE_THE_PASSWORD = _fake_secret("the")


_FAKE_PW = _fake_secret("pw")


_FAKE_DO_NOT_LEAK_ME_EITHER = _fake_secret("do")


_FAKE_DO_NOT_LEAK_ME = _fake_secret("do")


@pytest.fixture(autouse=True)
def prod_mode(monkeypatch):
    """Default every test to the real-AWS path; tests that want the mock path set
    infra.is_mock=True explicitly and keep this prod config (mirrors is_mock, not MODE)."""
    prod = SimpleNamespace(
        mode="prod", aws_access_key_id="PLATFORM_AKID", aws_secret_access_key="PLATFORM_SECRET",
    )
    monkeypatch.setattr(aws_session_mod, "app_config", prod)
    monkeypatch.setattr(pbc_mod, "app_config", prod)


@pytest.fixture
def make_infra_env(schema_db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, is_mock=False, exited=False, ecr_repository_url=None):
        user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
        infra = Infrastructure.objects.create(
            id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=2048, is_mock=is_mock, code=ACCOUNT_ID,
            is_cloud_authenticated=True, metadata={"aws_region": REGION},
            exited_at=datetime.now(timezone.utc) if exited else None,
        )
        env = Environment.objects.create(
            id=uuid.uuid4(), infrastructure=infra,
            ecr_repository_url=ecr_repository_url or f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{REPO_NAME}",
        )
        return infra, env
    return _make


def _sts_response(*, access_key="SCOPED_AK", secret_key="SCOPED_SK", token="SCOPED_TOKEN"):
    return {
        "Credentials": {
            "AccessKeyId": access_key,
            "SecretAccessKey": secret_key,
            "SessionToken": token,
            "Expiration": datetime.now(timezone.utc) + timedelta(minutes=15),
        }
    }


def _ecr_token_response(*, password=None, expires_at=None):
    password = password or _fake_secret("ecr")

    token = base64.b64encode(f"AWS:{password}".encode()).decode()
    return {
        "authorizationData": [
            {
                "authorizationToken": token,
                "expiresAt": expires_at or datetime.now(timezone.utc) + timedelta(hours=12),
                "proxyEndpoint": f"https://{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com",
            }
        ]
    }


def _patch_boto_clients(monkeypatch, *, sts=None, ecr=None, sts_error=None, ecr_error=None):
    sts_client = MagicMock()
    if sts_error:
        sts_client.assume_role.side_effect = sts_error
    else:
        sts_client.assume_role.return_value = sts or _sts_response()

    ecr_client = MagicMock()
    if ecr_error:
        ecr_client.get_authorization_token.side_effect = ecr_error
    else:
        ecr_client.get_authorization_token.return_value = ecr or _ecr_token_response()

    def _client(service_name, **kwargs):
        if service_name == "sts":
            _client.sts_kwargs = kwargs
            return sts_client
        if service_name == "ecr":
            _client.ecr_kwargs = kwargs
            return ecr_client
        raise AssertionError(f"unexpected service: {service_name}")

    monkeypatch.setattr(pbc_mod.boto3, "client", _client)
    return _client, sts_client, ecr_client


def _access_denied(action: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "AccessDenied", "Message": f"not authorized to perform: {action}"}},
        action,
    )


# ── session policy shape ─────────────────────────────────────────────────────────────

def test_session_policy_exact_shape_and_resource_arn(make_infra_env, monkeypatch):
    infra, _env = make_infra_env()
    _client, sts_client, _ecr = _patch_boto_clients(monkeypatch)

    mint_push_credential(infra, REPO_NAME)

    kwargs = sts_client.assume_role.call_args.kwargs
    assert kwargs["RoleArn"] == f"arn:aws:iam::{ACCOUNT_ID}:role/LaunchpadDeploymentRole"
    assert kwargs["ExternalId"] == str(infra.id)
    assert kwargs["DurationSeconds"] == 900

    import json
    policy = json.loads(kwargs["Policy"])
    assert policy["Statement"][0] == {
        "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*",
    }
    push_statement = policy["Statement"][1]
    assert push_statement["Resource"] == f"arn:aws:ecr:{REGION}:{ACCOUNT_ID}:repository/{REPO_NAME}"
    assert set(push_statement["Action"]) == {
        "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
    }
    # Never widened to this account's other repositories or another account.
    assert "*" not in push_statement["Resource"]


def test_session_assumed_from_platform_keys_not_customer_session(make_infra_env, monkeypatch):
    """The narrowing AssumeRole must come from the platform's own IAM user keys, not a
    session already scoped to this customer — asserting the exact keys passed catches a
    regression where someone wires this through the wide worker session instead."""
    infra, _env = make_infra_env()
    client, _sts_client, _ecr = _patch_boto_clients(monkeypatch)

    mint_push_credential(infra, REPO_NAME)

    assert client.sts_kwargs["aws_access_key_id"] == "PLATFORM_AKID"
    assert client.sts_kwargs["aws_secret_access_key"] == "PLATFORM_SECRET"


# ── only the narrow credential leaves the module ────────────────────────────────────

def test_returns_only_registry_username_password_expiry(make_infra_env, monkeypatch):
    infra, _env = make_infra_env()
    _patch_boto_clients(monkeypatch, ecr=_ecr_token_response(password=_FAKE_THE_PASSWORD))

    cred = mint_push_credential(infra, REPO_NAME)

    assert isinstance(cred, PushCredential)
    assert cred.registry == f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com"
    assert cred.username == "AWS"
    assert cred.password == _FAKE_THE_PASSWORD
    assert isinstance(cred.expires_at, datetime)
    # Never the underlying STS triple.
    assert not hasattr(cred, "access_key")
    assert not hasattr(cred, "session_token")


def test_expires_at_is_clamped_to_the_sooner_of_sts_and_ecr_expiry(make_infra_env, monkeypatch):
    """plan/F7-platform-builder.md's Credential flow #6 is an open, unverified question:
    whether an ECR auth token minted from a 900s STS session outlives that session or
    dies with it. Until confirmed against real AWS, this must report the pessimistic
    (sooner) bound rather than the ECR token's own nominal ~12h — a caller building
    timeout/retry logic on this field must not be told it has hours when it may have
    minutes."""
    sts_expiry = datetime.now(timezone.utc) + timedelta(minutes=15)
    ecr_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
    sts_response = _sts_response()
    sts_response["Credentials"]["Expiration"] = sts_expiry
    _patch_boto_clients(
        monkeypatch, sts=sts_response, ecr=_ecr_token_response(expires_at=ecr_expiry),
    )
    infra, _env = make_infra_env()

    cred = mint_push_credential(infra, REPO_NAME)

    assert cred.expires_at == sts_expiry


def test_docker_config_json_shape(make_infra_env, monkeypatch):
    infra, _env = make_infra_env()
    _patch_boto_clients(monkeypatch, ecr=_ecr_token_response(password=_FAKE_PW))

    cred = mint_push_credential(infra, REPO_NAME)
    config = cred.docker_config_json()

    assert config == {
        "auths": {cred.registry: {"auth": base64.b64encode(f"AWS:{_FAKE_PW}".encode()).decode()}}
    }


# ── redaction ────────────────────────────────────────────────────────────────────────

def test_password_never_appears_in_repr_or_str(make_infra_env, monkeypatch):
    infra, _env = make_infra_env()
    _patch_boto_clients(monkeypatch, ecr=_ecr_token_response(password=_FAKE_DO_NOT_LEAK_ME))

    cred = mint_push_credential(infra, REPO_NAME)

    assert _FAKE_DO_NOT_LEAK_ME not in repr(cred)
    assert _FAKE_DO_NOT_LEAK_ME not in str(cred)
    assert "***" in repr(cred)


def test_password_never_logged(make_infra_env, monkeypatch, caplog):
    infra, _env = make_infra_env()
    _patch_boto_clients(monkeypatch, ecr=_ecr_token_response(password=_FAKE_DO_NOT_LEAK_ME_EITHER))

    with caplog.at_level(logging.DEBUG):
        mint_push_credential(infra, REPO_NAME)

    for record in caplog.records:
        assert _FAKE_DO_NOT_LEAK_ME_EITHER not in record.getMessage()
        assert _FAKE_DO_NOT_LEAK_ME_EITHER not in str(record.__dict__)


# ── refusals ─────────────────────────────────────────────────────────────────────────

def test_refuses_mock_infra_in_real_mode(make_infra_env, monkeypatch):
    infra, _env = make_infra_env(is_mock=True)
    _patch_boto_clients(monkeypatch)

    with pytest.raises(ValueError, match="misconfigured"):
        mint_push_credential(infra, REPO_NAME)


def test_refuses_real_infra_in_dev_mode(make_infra_env, monkeypatch):
    infra, _env = make_infra_env(is_mock=False)
    monkeypatch.setattr(aws_session_mod, "app_config", SimpleNamespace(mode="dev"))
    monkeypatch.setattr(pbc_mod, "app_config", SimpleNamespace(mode="dev"))
    _patch_boto_clients(monkeypatch)

    with pytest.raises(ValueError, match="misconfigured"):
        mint_push_credential(infra, REPO_NAME)


def test_mock_infra_mints_synthetic_credential_without_touching_aws(make_infra_env, monkeypatch):
    infra, _env = make_infra_env(is_mock=True)
    dev = SimpleNamespace(mode="dev", aws_access_key_id="PLATFORM_AKID", aws_secret_access_key="PLATFORM_SECRET")
    monkeypatch.setattr(aws_session_mod, "app_config", dev)
    monkeypatch.setattr(pbc_mod, "app_config", dev)

    def _boom(*a, **k):
        raise AssertionError("mock infra must never call a real AWS client")

    monkeypatch.setattr(pbc_mod.boto3, "client", _boom)

    cred = mint_push_credential(infra, REPO_NAME)

    assert cred.username == "AWS"
    assert cred.password
    assert cred.registry == f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com"


def test_refuses_exited_infra(make_infra_env, monkeypatch):
    infra, _env = make_infra_env(exited=True)
    _patch_boto_clients(monkeypatch)

    with pytest.raises(InfrastructureExitedError):
        mint_push_credential(infra, REPO_NAME)


def test_rejects_malformed_repository_name(make_infra_env, monkeypatch):
    infra, _env = make_infra_env()
    _patch_boto_clients(monkeypatch)

    with pytest.raises(InvalidPushRepositoryError):
        mint_push_credential(infra, "Not/A valid-name!")


def test_rejects_foreign_repository_name(make_infra_env, monkeypatch):
    """A syntactically valid ECR name that just isn't this infra's own repository must
    still be refused — the session-policy Resource ARN is built from this value."""
    infra, _env = make_infra_env()
    _patch_boto_clients(monkeypatch)

    with pytest.raises(InvalidPushRepositoryError):
        mint_push_credential(infra, "some-other-infras-repo")


def test_rejects_when_infra_has_no_ecr_repository(make_infra_env, monkeypatch):
    infra, env = make_infra_env()
    env.ecr_repository_url = None
    env.save(update_fields=["ecr_repository_url"])
    _patch_boto_clients(monkeypatch)

    with pytest.raises(InvalidPushRepositoryError):
        mint_push_credential(infra, REPO_NAME)


# ── AccessDenied mapping ─────────────────────────────────────────────────────────────

def test_access_denied_on_assume_role_maps_to_policy_refresh_required(make_infra_env, monkeypatch):
    infra, _env = make_infra_env()
    _patch_boto_clients(monkeypatch, sts_error=_access_denied("sts:AssumeRole"))

    with pytest.raises(PolicyRefreshRequiredError):
        mint_push_credential(infra, REPO_NAME)


def test_access_denied_on_get_authorization_token_maps_to_policy_refresh_required(make_infra_env, monkeypatch):
    infra, _env = make_infra_env()
    _patch_boto_clients(monkeypatch, ecr_error=_access_denied("ecr:GetAuthorizationToken"))

    with pytest.raises(PolicyRefreshRequiredError):
        mint_push_credential(infra, REPO_NAME)


def test_other_client_error_is_not_swallowed(make_infra_env, monkeypatch):
    infra, _env = make_infra_env()
    throttled = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "AssumeRole",
    )
    _patch_boto_clients(monkeypatch, sts_error=throttled)

    with pytest.raises(ClientError):
        mint_push_credential(infra, REPO_NAME)
