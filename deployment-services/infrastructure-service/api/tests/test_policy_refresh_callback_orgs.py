"""Policy-refresh callback with ORGS_ENABLED on (F9 ticket 8).

The callback authenticates with a per-user script API key and has no JWT, so the key records
the Account its issuing session named. With the flag on, the infra link is accepted only when
that Account is the org's active owner right now, not merely the infra's creator."""
import uuid

import pytest
from api.tests.org_authz_support import make_infra, make_org
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.utils.jwt import JWTUser


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def orgs_on(settings):
    settings.ORGS_ENABLED = True


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="tester",
        )
    return _make


def _member(org, account_id, role, *, removed=False):
    from api.models.membership import Membership

    return Membership.objects.create(
        organization=org, account_id=account_id, role=role, version=1,
        removed_at=timezone.now() if removed else None,
    )


def _issue_key(factory, user, account_id):
    from api.views.script_api_key import script_api_key_issue

    request = factory.post("/api/v1/infrastructures/script-api-key/")
    claims = {"sub": str(user.id)}
    if account_id:
        claims["account_id"] = str(account_id)
    force_authenticate(request, user=JWTUser(**claims))
    return script_api_key_issue(request).data["api_key"]


def _callback(factory, api_key, **payload):
    from api.views.script_api_key import infrastructure_policy_refresh_callback

    payload.setdefault("account_id", "123456789012")
    request = factory.post(
        "/api/v1/infrastructures/policy-refresh/callback/", payload,
        format="json", HTTP_X_API_KEY=api_key,
    )
    return infrastructure_policy_refresh_callback(request)


def _events():
    from api.models.policy_refresh_event import PolicyRefreshEvent

    return PolicyRefreshEvent.objects.count()


def test_issue_records_the_signed_account_claim_when_the_flag_is_on(orgs_on, factory, make_user):
    from api.models.script_api_key import ScriptApiKey

    user, account = make_user(), uuid.uuid4()
    plaintext = _issue_key(factory, user, account)

    assert ScriptApiKey.authenticate(plaintext).account_id == account


def test_issue_records_no_account_when_the_flag_is_off(settings, factory, make_user):
    from api.models.script_api_key import ScriptApiKey

    settings.ORGS_ENABLED = False
    user = make_user()
    plaintext = _issue_key(factory, user, uuid.uuid4())

    assert ScriptApiKey.authenticate(plaintext).account_id is None


def test_the_current_org_owner_can_record_and_link_the_infra(orgs_on, factory, make_user):
    from api.models.policy_refresh_event import PolicyRefreshEvent

    org, account, user = make_org(), uuid.uuid4(), make_user()
    _member(org, account, "owner")
    infra = make_infra(owner=user, organization=org)

    response = _callback(factory, _issue_key(factory, user, account), infra_id=str(infra.id))

    assert response.status_code == 201
    assert PolicyRefreshEvent.objects.get(id=response.data["event_id"]).infrastructure_id == infra.id


def test_an_owner_who_did_not_create_the_infra_is_accepted(orgs_on, factory, make_user):
    org, owner_account = make_org(), uuid.uuid4()
    _member(org, owner_account, "owner")
    admin_creator, owner_user = make_user(), make_user()
    infra = make_infra(owner=admin_creator, organization=org)

    response = _callback(factory, _issue_key(factory, owner_user, owner_account), infra_id=str(infra.id))

    assert response.status_code == 201


def test_the_creator_is_refused_once_ownership_moved_to_someone_else(orgs_on, factory, make_user):
    org, creator_account, creator = make_org(), uuid.uuid4(), make_user()
    membership = _member(org, creator_account, "owner")
    infra = make_infra(owner=creator, organization=org)
    plaintext = _issue_key(factory, creator, creator_account)
    membership.role = "admin"
    membership.save()
    _member(org, uuid.uuid4(), "owner")

    response = _callback(factory, plaintext, infra_id=str(infra.id))

    assert response.status_code == 403
    assert _events() == 0


def test_a_creator_removed_from_the_org_is_refused(orgs_on, factory, make_user):
    org, account, creator = make_org(), uuid.uuid4(), make_user()
    _member(org, account, "owner", removed=True)
    infra = make_infra(owner=creator, organization=org)

    response = _callback(factory, _issue_key(factory, creator, account), infra_id=str(infra.id))

    assert response.status_code == 403
    assert _events() == 0


def test_an_admin_who_created_the_infra_is_refused(orgs_on, factory, make_user):
    org, admin_account, admin = make_org(), uuid.uuid4(), make_user()
    _member(org, uuid.uuid4(), "owner")
    _member(org, admin_account, "admin")
    infra = make_infra(owner=admin, organization=org)

    response = _callback(factory, _issue_key(factory, admin, admin_account), infra_id=str(infra.id))

    assert response.status_code == 403


def test_a_key_with_no_recorded_account_is_never_an_owner_key(orgs_on, factory, make_user):
    org, creator = make_org(), make_user()
    _member(org, uuid.uuid4(), "owner")
    infra = make_infra(owner=creator, organization=org)

    response = _callback(factory, _issue_key(factory, creator, None), infra_id=str(infra.id))

    assert response.status_code == 403


def test_an_owner_of_another_org_cannot_name_this_infra(orgs_on, factory, make_user):
    org, other_org, account, user = make_org(), make_org(), uuid.uuid4(), make_user()
    _member(other_org, account, "owner")
    infra = make_infra(owner=make_user(), organization=org)

    response = _callback(factory, _issue_key(factory, user, account), infra_id=str(infra.id))

    assert response.status_code == 403


def test_an_infra_with_no_org_is_refused(orgs_on, factory, make_user):
    org, account, user = make_org(), uuid.uuid4(), make_user()
    _member(org, account, "owner")
    infra = make_infra(owner=user, organization=None)

    response = _callback(factory, _issue_key(factory, user, account), infra_id=str(infra.id))

    assert response.status_code == 403


def test_unknown_and_foreign_infra_ids_get_the_same_403(orgs_on, factory, make_user):
    org, account, user = make_org(), uuid.uuid4(), make_user()
    _member(org, uuid.uuid4(), "owner")
    foreign = make_infra(owner=make_user(), organization=org)
    plaintext = _issue_key(factory, user, account)

    unknown = _callback(factory, plaintext, infra_id=str(uuid.uuid4()))
    existing = _callback(factory, plaintext, infra_id=str(foreign.id))

    assert unknown.status_code == existing.status_code == 403
    assert unknown.data == existing.data


def test_a_malformed_infra_id_is_still_recorded_unlinked(orgs_on, factory, make_user):
    from api.models.policy_refresh_event import PolicyRefreshEvent

    response = _callback(factory, _issue_key(factory, make_user(), uuid.uuid4()), infra_id="not-a-uuid")

    assert response.status_code == 201
    assert PolicyRefreshEvent.objects.get(id=response.data["event_id"]).infrastructure_id is None


def test_flag_off_the_creator_is_still_matched_without_any_membership(settings, factory, make_user):
    from api.models.policy_refresh_event import PolicyRefreshEvent

    settings.ORGS_ENABLED = False
    creator = make_user()
    infra = make_infra(owner=creator)

    response = _callback(factory, _issue_key(factory, creator, None), infra_id=str(infra.id))

    assert response.status_code == 201
    assert PolicyRefreshEvent.objects.get(id=response.data["event_id"]).infrastructure_id == infra.id
