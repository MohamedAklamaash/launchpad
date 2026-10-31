"""The real middleware chain (JWTAuthMiddleware then UserVerificationMiddleware) must hand
views a request.user that OrgPermissions.get_role can resolve a role from. In this service
UserVerificationMiddleware swaps the JWT user for the DB row; before the fix it dropped the
org_id / account_id claims and every org check resolved to no role."""
import time
import uuid

import jwt
import pytest
from rest_framework.test import APIRequestFactory
from shared.middleware.authentication import JWTAuthMiddleware
from shared.utils.org_id import personal_org_id

from api.middleware.user_verification import UserVerificationMiddleware
from api.tests.org_authz_support import (
    add_member,
    make_db_user,
    make_org,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _signing_key(settings):
    settings.JWT_SECRET = uuid.uuid4().hex + uuid.uuid4().hex


def _bearer(settings, **claims):
    claims.setdefault("exp", int(time.time()) + 600)
    return "Bearer " + jwt.encode(claims, settings.JWT_SECRET, algorithm="HS256")


def _run_chain(settings, **claims):
    seen = {}

    def view(request):
        seen["user"] = request.user
        from django.http import HttpResponse
        return HttpResponse("ok")

    chain = JWTAuthMiddleware(UserVerificationMiddleware(view))
    request = APIRequestFactory().get("/api/v1/applications/", HTTP_AUTHORIZATION=_bearer(settings, **claims))
    response = chain(request)
    return response, seen.get("user")


def test_org_claims_survive_the_user_swap_and_resolve_a_role(settings, fake_role_cache, schema_db):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org = make_org()
    account_id = add_member(org, "developer")
    db_user = make_db_user()

    response, user = _run_chain(settings, sub=str(db_user.id), org_id=str(org.id), account_id=str(account_id))

    assert response.status_code == 200
    assert user.pk == db_user.pk
    assert OrgPermissions.get_role(user, org.id) == "developer"


def test_pre_f9_token_resolves_to_the_personal_org_but_no_role_without_account_id(settings, fake_role_cache, schema_db):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    db_user = make_db_user()

    _, user = _run_chain(settings, sub=str(db_user.id))

    assert str(user.org_id) == str(personal_org_id(db_user.id))
    assert OrgPermissions.get_role(user, user.org_id) is None


def test_flag_off_leaves_the_db_user_untouched(settings, schema_db):
    settings.ORGS_ENABLED = False
    db_user = make_db_user()

    _, user = _run_chain(settings, sub=str(db_user.id), org_id=str(uuid.uuid4()), account_id=str(uuid.uuid4()))

    assert not hasattr(user, "org_id")
    assert not hasattr(user, "account_id")
