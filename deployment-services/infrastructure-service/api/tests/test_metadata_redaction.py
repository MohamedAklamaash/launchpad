"""Locks the fix for the credential-disclosure bug: assumed-role STS credentials
are stored in infrastructure.metadata for the provisioning worker, but must never
be serialized back to API callers."""
import uuid
from datetime import datetime, timezone

from api.types.infrastructure import InfrastructureResponse, _redact_metadata


def _response(metadata):
    return InfrastructureResponse(
        id=uuid.uuid4(),
        name="infra",
        user_id=uuid.uuid4(),
        cloud_provider="aws",
        max_cpu=1024,
        max_memory=512,
        is_cloud_authenticated=True,
        metadata=metadata,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        invited_users=[],
    )


def test_to_dict_strips_sts_credentials():
    data = _response({
        "aws_access_key_id": "AKIA_SECRET",
        "aws_secret_access_key": "super-secret",
        "aws_session_token": "tok",
        "expiration": "2026-01-01T00:00:00Z",
        "aws_region": "us-east-1",
        "vpc_cidr": "10.0.0.0/16",
    }).to_dict()
    meta = data["metadata"]
    assert "aws_secret_access_key" not in meta
    assert "aws_access_key_id" not in meta
    assert "aws_session_token" not in meta
    assert "expiration" not in meta
    # Non-sensitive config is preserved.
    assert meta["aws_region"] == "us-east-1"
    assert meta["vpc_cidr"] == "10.0.0.0/16"


def test_redact_handles_none_and_empty():
    assert _redact_metadata(None) is None
    assert _redact_metadata({}) == {}


def test_no_credential_substring_in_serialized_output():
    data = _response({"aws_secret_access_key": "leakme"}).to_dict()
    assert "leakme" not in str(data)
