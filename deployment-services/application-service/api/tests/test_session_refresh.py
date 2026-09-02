from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock


def _sts_returning(access_key, expiry):
    sts = MagicMock()
    sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": access_key,
            "SecretAccessKey": "SK",
            "SessionToken": "TK",
            "Expiration": expiry,
        }
    }
    return sts


def test_assume_role_raw_persists_and_returns(monkeypatch):
    from aws import session as S

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    sts = _sts_returning("AK_NEW", future)
    monkeypatch.setattr(S.boto3, "client", lambda *a, **k: sts)

    infra = SimpleNamespace(code="123456789012", id="i1", metadata={}, save=MagicMock())
    out = S._assume_role_raw(infra)

    assert out == {"access_key": "AK_NEW", "secret_key": "SK", "token": "TK", "expiry_time": future.isoformat()}
    assert infra.metadata["aws_access_key_id"] == "AK_NEW"
    infra.save.assert_called_once()


def test_valid_seed_is_used_without_reassuming(monkeypatch):
    from aws import session as S

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    sts = _sts_returning("AK_REFRESHED", future)
    monkeypatch.setattr(S.boto3, "client", lambda *a, **k: sts)

    infra = SimpleNamespace(
        code="123456789012", id="i1",
        metadata={
            "aws_access_key_id": "AK_SEED", "aws_secret_access_key": "SS",
            "aws_session_token": "ST", "expiration": future.isoformat(), "aws_region": "us-east-1",
        },
        save=MagicMock(),
    )
    frozen = S._build_real_session(infra).get_credentials().get_frozen_credentials()

    assert frozen.access_key == "AK_SEED"
    sts.assume_role.assert_not_called()


def test_expired_seed_triggers_reassume(monkeypatch):
    from aws import session as S

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    sts = _sts_returning("AK_REFRESHED", future)
    monkeypatch.setattr(S.boto3, "client", lambda *a, **k: sts)

    infra = SimpleNamespace(
        code="123456789012", id="i1",
        metadata={
            "aws_access_key_id": "AK_EXPIRED", "aws_secret_access_key": "SS",
            "aws_session_token": "ST", "expiration": past.isoformat(), "aws_region": "us-east-1",
        },
        save=MagicMock(),
    )
    frozen = S._build_real_session(infra).get_credentials().get_frozen_credentials()

    assert frozen.access_key == "AK_REFRESHED"
    sts.assume_role.assert_called()
