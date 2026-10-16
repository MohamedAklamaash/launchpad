"""F9 ticket 5: shared.middleware.authentication.JWTAuthMiddleware gains the
pre-F9-token-resolves-to-the-personal-org fallback (plan/F9-teams-and-orgs.md,
Decision: JWT and authorization), gated entirely on ORGS_ENABLED (scope item 4: flag
off means no request-behavior change at all).
"""
import json

import jwt
from django.test import RequestFactory
from django.test.utils import override_settings
from shared.middleware.authentication import JWTAuthMiddleware
from shared.utils.org_id import personal_org_id

SECRET = "x" * 40
LEGACY_USER_ID = "019f2c2b-9b19-71a9-8f3e-cd81d13effb9"


def _middleware():
    captured = {}

    def _get_response(request):
        captured["user"] = request.user
        return "downstream-called"

    middleware = JWTAuthMiddleware(get_response=_get_response)
    return middleware, captured


def _authed_request(token):
    request = RequestFactory().get("/api/v1/infrastructures/")
    request.headers = {"Authorization": f"Bearer {token}"}
    return request


@override_settings(JWT_SECRET=SECRET, ORGS_ENABLED=False)
def test_flag_off_never_sets_org_id_even_for_a_token_with_no_claim():
    token = jwt.encode({"sub": LEGACY_USER_ID}, SECRET, algorithm="HS256")
    middleware, captured = _middleware()
    response = middleware(_authed_request(token))

    assert response == "downstream-called"
    assert not hasattr(captured["user"], "org_id")


@override_settings(JWT_SECRET=SECRET, ORGS_ENABLED=True)
def test_flag_on_token_with_no_org_id_claim_resolves_to_the_personal_org():
    token = jwt.encode({"sub": LEGACY_USER_ID}, SECRET, algorithm="HS256")
    middleware, captured = _middleware()
    response = middleware(_authed_request(token))

    assert response == "downstream-called"
    assert captured["user"].org_id == str(personal_org_id(LEGACY_USER_ID))


@override_settings(JWT_SECRET=SECRET, ORGS_ENABLED=True)
def test_flag_on_token_with_an_org_id_claim_keeps_the_claimed_value_unchanged():
    switched_org_id = "11111111-1111-1111-1111-111111111111"
    token = jwt.encode({"sub": LEGACY_USER_ID, "org_id": switched_org_id}, SECRET, algorithm="HS256")
    middleware, captured = _middleware()
    response = middleware(_authed_request(token))

    assert response == "downstream-called"
    # NOT personal_org_id(LEGACY_USER_ID) — a real org switch must never be silently
    # overridden back to the personal org.
    assert captured["user"].org_id == switched_org_id


@override_settings(JWT_SECRET=SECRET, ORGS_ENABLED=True)
def test_flag_on_a_malformed_org_id_claim_is_rejected_401_not_silently_accepted():
    token = jwt.encode({"sub": LEGACY_USER_ID, "org_id": "not-a-uuid"}, SECRET, algorithm="HS256")
    middleware, _captured = _middleware()
    response = middleware(_authed_request(token))

    assert response.status_code == 401
    assert json.loads(response.content) == {
        "message": "Invalid organization scope", "details": None,
    }


@override_settings(JWT_SECRET=SECRET, ORGS_ENABLED=True)
def test_flag_on_account_id_claim_passes_through_unmodified():
    """The middleware never computes or injects account_id — it only ever trusts the
    claim as presented (or its absence), since there is no formula to derive it
    without a DB lookup the way org_id's personal-org fallback has."""
    token = jwt.encode(
        {"sub": LEGACY_USER_ID, "account_id": "9762168d-fbdc-5ee1-aa27-39ad30b6223f"},
        SECRET,
        algorithm="HS256",
    )
    middleware, captured = _middleware()
    middleware(_authed_request(token))

    assert captured["user"].account_id == "9762168d-fbdc-5ee1-aa27-39ad30b6223f"
