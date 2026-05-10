import sys
import types
import uuid
from unittest.mock import patch, MagicMock

import pytest
from rest_framework.test import APIRequestFactory


# Stub the InfraQueue module BEFORE the view imports it. The real module touches
# Redis at import-time (creates connection pools), which we don't want in unit
# tests.
fake_infra_queue_mod = types.ModuleType("api.services.infra_queue")
fake_infra_queue_mod.InfraQueue = MagicMock()
sys.modules.setdefault("api.services.infra_queue", fake_infra_queue_mod)


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


def test_happy_path_returns_202_and_enqueues_provision(
    factory, view, make_infra
):
    infra = make_infra(code="123456789012")
    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure"
    ) as mock_auth, patch(
        "api.services.infra_queue.InfraQueue"
    ) as mock_queue:
        mock_auth.return_value = None
        request = factory.post(
            "/api/v1/infrastructures/onboarding/callback/",
            {"infra_id": str(infra.id), "account_id": "123456789012"},
            format="json",
        )
        response = view(request)
    assert response.status_code == 202
    assert response.data["is_cloud_authenticated"] is True
    assert response.data["infrastructure_id"] == str(infra.id)
    mock_queue.enqueue_provision.assert_called_once_with(str(infra.id))


def test_returns_403_when_authenticate_raises_generic_exception(
    factory, view, make_infra
):
    infra = make_infra(code="123456789012")
    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure"
    ) as mock_auth:
        mock_auth.side_effect = RuntimeError("boom")
        request = factory.post(
            "/api/v1/infrastructures/onboarding/callback/",
            {"infra_id": str(infra.id), "account_id": "123456789012"},
            format="json",
        )
        response = view(request)
    assert response.status_code == 403
    assert response.data["error"] == "AssumeRole failed"
    assert response.data["details"] == "RuntimeError"
    assert response.data["retry_after_seconds"] == 30


def test_returns_500_when_enqueue_provision_raises(factory, view, make_infra):
    infra = make_infra(code="123456789012")
    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure"
    ) as mock_auth, patch(
        "api.services.infra_queue.InfraQueue"
    ) as mock_queue:
        mock_auth.return_value = None
        mock_queue.enqueue_provision.side_effect = RuntimeError("redis down")
        request = factory.post(
            "/api/v1/infrastructures/onboarding/callback/",
            {"infra_id": str(infra.id), "account_id": "123456789012"},
            format="json",
        )
        response = view(request)
    assert response.status_code == 500
    assert response.data["error"] == "Internal error"
