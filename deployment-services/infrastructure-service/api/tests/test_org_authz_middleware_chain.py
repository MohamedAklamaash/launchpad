"""JWTAuthMiddleware leaves a request.user that OrgPermissions.get_role can resolve a role
from, end to end through a real signed token (the key is generated per test)."""
import time
import uuid

import jwt
import pytest
from api.tests.org_authz_support import (
    add_member,
    make_org,
)
from django.http import HttpResponse
from rest_framework.test import APIRequestFactory
from shared.middleware.authentication import JWTAuthMiddleware
from shared.utils.org_id import personal_org_id

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _signing_key(settings):
    settings.JWT_SECRET = uuid.uuid4().hex + uuid.uuid4().hex


def _run_chain(settings, **claims):
    seen = {}

    def view(request):
        seen["user"] = request.user
        return HttpResponse("ok")

    claims.setdefault("exp", int(time.time()) + 600)
    token = jwt.encode(claims, settings.JWT_SECRET, algorithm="HS256")
    request = APIRequestFactory().get("/api/v1/infrastructures/", HTTP_AUTHORIZATION=f"Bearer {token}")
    response = JWTAuthMiddleware(view)(request)
    return response, seen.get("user")


def test_signed_org_claims_resolve_a_role(settings, fake_role_cache):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org = make_org()
    account_id = add_member(org, "admin")

    response, user = _run_chain(settings, sub=str(uuid.uuid4()), org_id=str(org.id), account_id=str(account_id))

    assert response.status_code == 200
    assert OrgPermissions.get_role(user, org.id) == "admin"


def test_pre_f9_token_gets_the_personal_org_and_no_role(settings, fake_role_cache):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    sub = uuid.uuid4()

    _, user = _run_chain(settings, sub=str(sub))

    assert str(user.org_id) == str(personal_org_id(sub))
    assert OrgPermissions.get_role(user, user.org_id) is None
