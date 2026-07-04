import uuid

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def callback_view():
    from api.views.script_api_key import infrastructure_policy_refresh_callback
    return infrastructure_policy_refresh_callback


@pytest.fixture
def issue_view():
    from api.views.script_api_key import script_api_key_issue
    return script_api_key_issue


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(),
            email=f"u-{uuid.uuid4()}@example.com",
            user_name="tester",
        )
    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(user=None, *, code="123456789012"):
        return Infrastructure.objects.create(
            user=user or make_user(),
            name=f"infra-{uuid.uuid4()}",
            cloud_provider="aws",
            max_cpu=1024,
            max_memory=512,
            code=code,
            metadata={},
        )
    return _make


def _post_callback(factory, view, *, api_key=None, **payload):
    payload.setdefault("account_id", "123456789012")
    headers = {"HTTP_X_API_KEY": api_key} if api_key else {}
    request = factory.post(
        "/api/v1/infrastructures/policy-refresh/callback/", payload,
        format="json", **headers,
    )
    return view(request)


# ── script_api_key_issue ──────────────────────────────────────────────────────

def test_issue_returns_plaintext_once_and_stores_only_hash(factory, issue_view, make_user):
    from api.models.script_api_key import ScriptApiKey

    user = make_user()
    request = factory.post("/api/v1/infrastructures/script-api-key/")
    force_authenticate(request, user=user)
    response = issue_view(request)
    assert response.status_code == 201
    plaintext = response.data["api_key"]
    assert plaintext.startswith("lp_")
    key = ScriptApiKey.objects.get(user=user)
    assert key.key_hash == ScriptApiKey.hash_key(plaintext)
    assert plaintext not in key.key_hash


def test_issue_rotates_previous_keys(factory, issue_view, make_user):
    from api.models.script_api_key import ScriptApiKey

    user = make_user()
    first = factory.post("/api/v1/infrastructures/script-api-key/")
    force_authenticate(first, user=user)
    old_plaintext = issue_view(first).data["api_key"]

    second = factory.post("/api/v1/infrastructures/script-api-key/")
    force_authenticate(second, user=user)
    new_plaintext = issue_view(second).data["api_key"]

    assert ScriptApiKey.authenticate(old_plaintext) is None
    active = ScriptApiKey.authenticate(new_plaintext)
    assert active is not None and active.user_id == user.id


# ── infrastructure_policy_refresh_callback ────────────────────────────────────

def test_callback_returns_401_without_api_key(factory, callback_view, db):
    response = _post_callback(factory, callback_view)
    assert response.status_code == 401


def test_callback_returns_401_with_invalid_api_key(factory, callback_view, db):
    response = _post_callback(factory, callback_view, api_key="lp_not-a-real-key")
    assert response.status_code == 401


def test_callback_returns_400_without_account_id(factory, callback_view, make_user):
    from api.models.script_api_key import ScriptApiKey

    plaintext = ScriptApiKey.issue(make_user())
    request = factory.post(
        "/api/v1/infrastructures/policy-refresh/callback/", {},
        format="json", HTTP_X_API_KEY=plaintext,
    )
    response = callback_view(request)
    assert response.status_code == 400
    assert "account_id" in response.data["error"]


def test_callback_records_event_with_user_attribution(factory, callback_view, make_user):
    from api.models.policy_refresh_event import PolicyRefreshEvent
    from api.models.script_api_key import ScriptApiKey

    user = make_user()
    plaintext = ScriptApiKey.issue(user)
    response = _post_callback(
        factory, callback_view, api_key=plaintext,
        caller_arn="arn:aws:iam::123456789012:user/ops-engineer",
        script="create_aws_role.sh",
        role_name="LaunchpadDeploymentRole",
        policy_arn="arn:aws:iam::123456789012:policy/LaunchpadDeploymentPolicy",
    )
    assert response.status_code == 201
    event = PolicyRefreshEvent.objects.get(id=response.data["event_id"])
    assert event.user_id == user.id
    assert event.account_id == "123456789012"
    assert event.caller_arn == "arn:aws:iam::123456789012:user/ops-engineer"
    assert event.script == "create_aws_role.sh"
    key = ScriptApiKey.objects.get(user=user)
    assert key.last_used_at is not None


def test_callback_links_infrastructure_when_infra_id_given(
    factory, callback_view, make_user, make_infra
):
    from api.models.policy_refresh_event import PolicyRefreshEvent
    from api.models.script_api_key import ScriptApiKey

    user = make_user()
    infra = make_infra(user)
    plaintext = ScriptApiKey.issue(user)
    response = _post_callback(
        factory, callback_view, api_key=plaintext, infra_id=str(infra.id),
    )
    assert response.status_code == 201
    event = PolicyRefreshEvent.objects.get(id=response.data["event_id"])
    assert event.infrastructure_id == infra.id


def test_callback_still_records_with_unknown_or_malformed_infra_id(
    factory, callback_view, make_user
):
    from api.models.policy_refresh_event import PolicyRefreshEvent
    from api.models.script_api_key import ScriptApiKey

    plaintext = ScriptApiKey.issue(make_user())
    for bad_infra in (str(uuid.uuid4()), "not-a-uuid"):
        response = _post_callback(
            factory, callback_view, api_key=plaintext, infra_id=bad_infra,
        )
        assert response.status_code == 201
        event = PolicyRefreshEvent.objects.get(id=response.data["event_id"])
        assert event.infrastructure_id is None
