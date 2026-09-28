"""H3 R1: decode_jwt (shared.utils.jwt, used by every Django service's
JWTAuthMiddleware) must reject a token carrying a `scope` claim — currently
auth-service's 5-minute password_reset token, which still carries the holder's real
sub/role and must never be usable as a stand-in for a full session.
"""
import jwt
import pytest
from shared.utils.jwt import ScopedTokenRejected, decode_jwt

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
    # Every existing caller (JWTAuthMiddleware, DRF's JWTAuthentication) already
    # handles a malformed/expired token as "not authenticated" — ScopedTokenRejected
    # must fall into that same handling, not a new, unhandled exception type.
    assert issubclass(ScopedTokenRejected, jwt.InvalidTokenError)


def test_an_empty_scope_string_is_not_treated_as_scoped():
    # Falsy is not the same as absent, but an empty string was never a real purpose
    # value either — only a truthy scope should trigger rejection.
    user = decode_jwt(_token(scope=""), SECRET)
    assert user.id == "user-1"
