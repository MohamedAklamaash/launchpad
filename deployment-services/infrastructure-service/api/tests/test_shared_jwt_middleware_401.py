"""H7: shared.middleware.authentication.JWTAuthMiddleware used to let any
jwt.PyJWTError (expired, malformed, ScopedTokenRejected) fall through its bare
`except Exception` branch, returning a 500 with the exception text in `details`.
Any such error must instead be a 401 with a fixed, generic body.
"""
import json
import time

import jwt
from django.test import RequestFactory
from django.test.utils import override_settings
from shared.middleware.authentication import JWTAuthMiddleware

SECRET = "x" * 40


def _middleware():
    return JWTAuthMiddleware(get_response=lambda request: "downstream-called")


def _authed_request(token):
    request = RequestFactory().get("/api/v1/infrastructures/")
    request.headers = {"Authorization": f"Bearer {token}"}
    return request


@override_settings(JWT_SECRET=SECRET)
def test_expired_token_returns_generic_401():
    token = jwt.encode(
        {"sub": "user-1", "exp": int(time.time()) - 10},
        SECRET,
        algorithm="HS256",
    )
    response = _middleware()(_authed_request(token))

    assert response.status_code == 401
    assert json.loads(response.content) == {"message": "Invalid or expired token", "details": None}


@override_settings(JWT_SECRET=SECRET)
def test_malformed_token_returns_generic_401():
    response = _middleware()(_authed_request("not-a-jwt"))

    assert response.status_code == 401
    assert json.loads(response.content) == {"message": "Invalid or expired token", "details": None}


@override_settings(JWT_SECRET=SECRET)
def test_scoped_token_rejection_returns_generic_401():
    token = jwt.encode(
        {"sub": "user-1", "scope": "password_reset"},
        SECRET,
        algorithm="HS256",
    )
    response = _middleware()(_authed_request(token))

    assert response.status_code == 401
    assert json.loads(response.content) == {"message": "Invalid or expired token", "details": None}


@override_settings(JWT_SECRET=SECRET)
def test_valid_token_still_reaches_the_view():
    token = jwt.encode({"sub": "user-1"}, SECRET, algorithm="HS256")
    response = _middleware()(_authed_request(token))

    assert response == "downstream-called"
