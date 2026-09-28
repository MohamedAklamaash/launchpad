"""H3 R1: decode_jwt must reject a token carrying a `scope` claim — currently
auth-service's 5-minute password_reset token, which still carries the holder's real
sub/role and must never be usable as a stand-in for a full session.

Pure-function tests only — jwt.py has no Django dependency, so these run under plain
pytest without a settings module. payment-service has no other pytest suite/config yet;
see plan/H-hardening.md H3 for why this is scoped to just this module.
"""
import jwt
import pytest

from api.common.utils.jwt import ScopedTokenRejected, decode_jwt

SECRET = "x" * 40


def _token(**extra_claims):
    payload = {"sub": "user-1", "email": "user@example.com", "user_name": "user-1", "role": "user"}
    payload.update(extra_claims)
    return jwt.encode(payload, SECRET, algorithm="HS256")


def test_a_normal_session_token_decodes():
    user = decode_jwt(_token(), SECRET)
    assert user.id == "user-1"


def test_a_scoped_token_is_rejected():
    token = _token(scope="password_reset")
    with pytest.raises(ScopedTokenRejected):
        decode_jwt(token, SECRET)


def test_scoped_token_rejected_is_an_invalid_token_error():
    assert issubclass(ScopedTokenRejected, jwt.InvalidTokenError)


def test_an_empty_scope_string_is_not_treated_as_scoped():
    user = decode_jwt(_token(scope=""), SECRET)
    assert user.id == "user-1"
