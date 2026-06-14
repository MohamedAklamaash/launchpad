import sys
import types
import uuid
from unittest.mock import patch, MagicMock

import pytest
from rest_framework.test import APIRequestFactory


@pytest.fixture(autouse=True)
def _stub_infra_queue():
    """Stub the InfraQueue module the view imports lazily — the real module opens
    Redis connection pools at import time. Installed per-test and restored on
    teardown so it doesn't leak the fake into other test modules in the same run.
    """
    previous = sys.modules.get("api.services.infra_queue")
    fake = types.ModuleType("api.services.infra_queue")
    fake.InfraQueue = MagicMock()
    sys.modules["api.services.infra_queue"] = fake
    try:
        yield
    finally:
        if previous is not None:
            sys.modules["api.services.infra_queue"] = previous
        else:
            sys.modules.pop("api.services.infra_queue", None)


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def view():
    from api.views.infrastructure import infrastructure_onboarding_callback
    return infrastructure_onboarding_callback


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

    def _make(*, code="123456789012", cloud_provider="aws", metadata=None):
        return Infrastructure.objects.create(
            user=make_user(),
            name=f"infra-{uuid.uuid4()}",
            cloud_provider=cloud_provider,
            max_cpu=1024,
            max_memory=512,
            code=code,
            metadata=metadata or {},
        )
    return _make


def test_returns_400_when_body_is_empty(factory, view):
    request = factory.post("/api/v1/infrastructures/onboarding/callback/", {}, format="json")
    response = view(request)
    assert response.status_code == 400
    assert "infra_id" in response.data["error"]


def test_returns_400_when_infra_id_is_malformed_uuid(factory, view):
    request = factory.post(
        "/api/v1/infrastructures/onboarding/callback/",
        {"infra_id": "not-a-uuid", "account_id": "123456789012"},
        format="json",
    )
    response = view(request)
    assert response.status_code == 400
    assert "must be a valid UUID" in response.data["error"]


def test_returns_404_when_infra_does_not_exist(factory, view, db):
    request = factory.post(
        "/api/v1/infrastructures/onboarding/callback/",
        {"infra_id": str(uuid.uuid4()), "account_id": "123456789012"},
        format="json",
    )
    response = view(request)
    assert response.status_code == 404


def test_returns_403_when_account_id_does_not_match_infra_code(
    factory, view, make_infra
):
    infra = make_infra(code="111111111111")
    request = factory.post(
        "/api/v1/infrastructures/onboarding/callback/",
        {"infra_id": str(infra.id), "account_id": "999999999999"},
        format="json",
    )
    response = view(request)
    assert response.status_code == 403
    assert "Account ID mismatch" in response.data["error"]


def test_returns_400_when_infra_is_not_aws(factory, view, make_infra):
    infra = make_infra(code="123456789012", cloud_provider="azure")
    request = factory.post(
        "/api/v1/infrastructures/onboarding/callback/",
        {"infra_id": str(infra.id), "account_id": "123456789012"},
        format="json",
    )
    response = view(request)
    assert response.status_code == 400
    assert "Not an AWS infrastructure" in response.data["error"]


def _post_callback(factory, view, infra, token, **overrides):
    payload = {
        "infra_id": str(infra.id),
        "account_id": "123456789012",
        "onboarding_token": token,
    }
    payload.update(overrides)
    request = factory.post(
        "/api/v1/infrastructures/onboarding/callback/", payload, format="json"
    )
    return view(request)


def test_returns_400_when_onboarding_token_missing(factory, view, make_infra):
    infra = make_infra(code="123456789012")
    infra.issue_onboarding_token()
    request = factory.post(
        "/api/v1/infrastructures/onboarding/callback/",
        {"infra_id": str(infra.id), "account_id": "123456789012"},
        format="json",
    )
    response = view(request)
    assert response.status_code == 400
    assert "onboarding_token" in response.data["error"]


def test_returns_403_when_onboarding_token_is_wrong(factory, view, make_infra):
    infra = make_infra(code="123456789012")
    infra.issue_onboarding_token()
    response = _post_callback(factory, view, infra, "not-the-token")
    assert response.status_code == 403
    assert response.data["error"] == "Invalid onboarding token"


def test_returns_403_when_token_already_used(factory, view, make_infra):
    from django.utils import timezone

    infra = make_infra(code="123456789012")
    token = infra.issue_onboarding_token()
    infra.onboarding_token_used_at = timezone.now()
    infra.save(update_fields=["onboarding_token_used_at"])
    response = _post_callback(factory, view, infra, token)
    assert response.status_code == 403
    assert response.data["error"] == "Onboarding token already used"


def test_returns_403_when_token_expired(factory, view, make_infra):
    from datetime import timedelta
    from django.utils import timezone

    infra = make_infra(code="123456789012")
    token = infra.issue_onboarding_token()
    infra.onboarding_token_expires_at = timezone.now() - timedelta(minutes=1)
    infra.save(update_fields=["onboarding_token_expires_at"])
    response = _post_callback(factory, view, infra, token)
    assert response.status_code == 403
    assert "expired" in response.data["error"].lower()


def test_happy_path_returns_202_burns_token_and_enqueues_provision(
    factory, view, make_infra
):
    infra = make_infra(code="123456789012")
    token = infra.issue_onboarding_token()
    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure"
    ) as mock_auth, patch(
        "api.services.infra_queue.InfraQueue"
    ) as mock_queue:
        mock_auth.return_value = None
        response = _post_callback(factory, view, infra, token)
    assert response.status_code == 202
    assert response.data["is_cloud_authenticated"] is True
    assert response.data["infrastructure_id"] == str(infra.id)
    mock_queue.enqueue_provision.assert_called_once_with(str(infra.id))
    infra.refresh_from_db()
    assert infra.onboarding_token_used_at is not None


def test_returns_403_and_releases_token_when_authenticate_raises_generic_exception(
    factory, view, make_infra
):
    infra = make_infra(code="123456789012")
    token = infra.issue_onboarding_token()
    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure"
    ) as mock_auth, patch(
        "api.views.infrastructure.time.sleep"
    ):
        mock_auth.side_effect = RuntimeError("boom")
        response = _post_callback(factory, view, infra, token)
    assert response.status_code == 403
    assert response.data["error"] == "AssumeRole failed"
    assert response.data["details"] == "RuntimeError"
    assert response.data["retry_after_seconds"] == 30
    # Claim must be released so the customer can re-run the script.
    infra.refresh_from_db()
    assert infra.onboarding_token_used_at is None


def test_returns_500_and_releases_token_when_enqueue_provision_raises(
    factory, view, make_infra
):
    infra = make_infra(code="123456789012")
    token = infra.issue_onboarding_token()
    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure"
    ) as mock_auth, patch(
        "api.services.infra_queue.InfraQueue"
    ) as mock_queue:
        mock_auth.return_value = None
        mock_queue.enqueue_provision.side_effect = RuntimeError("redis down")
        response = _post_callback(factory, view, infra, token)
    assert response.status_code == 500
    assert response.data["error"] == "Internal error"
    infra.refresh_from_db()
    assert infra.onboarding_token_used_at is None
