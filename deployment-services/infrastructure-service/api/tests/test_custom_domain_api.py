"""Custom-domain HTTP API: claim/list/verify/delete against the real service and models
(mocked only at the cross-service HTTP boundary and AssumeRole — see
test_custom_domain_service.py for the service-layer test suite this exercises through).
Covers owner-only authz, cross-tenant 404, the budget 429, and the response shape the
dashboard's DNS-instructions UI depends on."""
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from api.services import cert_bootstrap, custom_domain_dns
from api.views.custom_domain import (
    custom_domain_detail,
    custom_domain_list_create,
    custom_domain_verify,
)
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture(autouse=True)
def _stub_rate_budget(monkeypatch):
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _reset_fakes():
    cert_bootstrap.reset_fake_acm_for_tests()
    custom_domain_dns.reset_fake_resolver_for_tests()
    yield
    cert_bootstrap.reset_fake_acm_for_tests()
    custom_domain_dns.reset_fake_resolver_for_tests()


@pytest.fixture(autouse=True)
def _mock_dev_mode():
    with patch("api.services.custom_domain_service.is_dev_mode", return_value=True), \
         patch("api.services.custom_domain_service.assume_role_credentials_only", return_value={}):
        yield


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )

    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(user=None, compute_type="ecs_fargate"):
        return Infrastructure.objects.create(
            user=user or make_user(), name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", is_mock=True,
            compute_type=compute_type,
        )

    return _make


def _ok_app_lookup(infra_id):
    return SimpleNamespace(status_code=200, json=lambda: {"infrastructure_id": str(infra_id)}, text="")


def _claim(factory, infra, application_id=None):
    request = factory.post(
        f"/api/v1/infrastructures/{infra.id}/custom-domains/",
        {"application_id": str(application_id or uuid.uuid4()), "hostname": "app.example.com"},
        format="json",
    )
    force_authenticate(request, user=SimpleNamespace(id=infra.user_id))
    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_ok_app_lookup(infra.id)):
        return custom_domain_list_create(request, str(infra.id))


def test_claim_returns_201_with_dns_instructions_and_token_once(factory, make_infra):
    infra = make_infra()

    response = _claim(factory, infra)

    assert response.status_code == 201
    body = response.data
    assert body["status"] == "PENDING"
    assert body["ownership_txt"]["name"] == f"_launchpad-challenge.{body['hostname']}"
    assert body["ownership_txt"]["value"]  # the plaintext token, shown once
    assert body["cname_to_edge"]["type"] == "CNAME"


def test_claim_rejects_invalid_body(factory, make_infra):
    infra = make_infra()
    request = factory.post(f"/api/v1/infrastructures/{infra.id}/custom-domains/", {}, format="json")
    force_authenticate(request, user=SimpleNamespace(id=infra.user_id))

    response = custom_domain_list_create(request, str(infra.id))

    assert response.status_code == 400


def test_claim_cross_tenant_returns_404(factory, make_infra, make_user):
    infra = make_infra()
    stranger = make_user()
    request = factory.post(
        f"/api/v1/infrastructures/{infra.id}/custom-domains/",
        {"application_id": str(uuid.uuid4()), "hostname": "app.example.com"}, format="json",
    )
    force_authenticate(request, user=SimpleNamespace(id=stranger.id))

    response = custom_domain_list_create(request, str(infra.id))

    assert response.status_code == 404


def test_claim_eks_infra_returns_400(factory, make_infra):
    infra = make_infra(compute_type="eks")
    request = factory.post(
        f"/api/v1/infrastructures/{infra.id}/custom-domains/",
        {"application_id": str(uuid.uuid4()), "hostname": "app.example.com"}, format="json",
    )
    force_authenticate(request, user=SimpleNamespace(id=infra.user_id))

    with patch("shared.resilience.http_client.ResilientHttpClient.get", return_value=_ok_app_lookup(infra.id)):
        response = custom_domain_list_create(request, str(infra.id))

    assert response.status_code == 400


def test_list_omits_ownership_token_value(factory, make_infra):
    infra = make_infra()
    claim_response = _claim(factory, infra)
    assert claim_response.status_code == 201

    request = factory.get(f"/api/v1/infrastructures/{infra.id}/custom-domains/")
    force_authenticate(request, user=SimpleNamespace(id=infra.user_id))

    response = custom_domain_list_create(request, str(infra.id))

    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["ownership_txt"]["value"] is None


def test_verify_success_returns_validated(factory, make_infra):
    infra = make_infra()
    claim_response = _claim(factory, infra)
    domain_id = claim_response.data["id"]
    token = claim_response.data["ownership_txt"]["value"]
    hostname = claim_response.data["hostname"]
    custom_domain_dns._shared_fake_resolver().set_txt(hostname, token)

    request = factory.post(f"/api/v1/infrastructures/{infra.id}/custom-domains/{domain_id}/verify/")
    force_authenticate(request, user=SimpleNamespace(id=infra.user_id))

    with patch("api.services.custom_domain_service.CustomDomainService._attach", return_value={}):
        response = custom_domain_verify(request, str(infra.id), str(domain_id))

    assert response.status_code == 200
    assert response.data["status"] == "VALIDATED"


def test_verify_without_txt_returns_400(factory, make_infra):
    infra = make_infra()
    claim_response = _claim(factory, infra)
    domain_id = claim_response.data["id"]

    request = factory.post(f"/api/v1/infrastructures/{infra.id}/custom-domains/{domain_id}/verify/")
    force_authenticate(request, user=SimpleNamespace(id=infra.user_id))

    response = custom_domain_verify(request, str(infra.id), str(domain_id))

    assert response.status_code == 400


def test_verify_cross_tenant_returns_404(factory, make_infra, make_user):
    infra = make_infra()
    claim_response = _claim(factory, infra)
    domain_id = claim_response.data["id"]
    stranger = make_user()

    request = factory.post(f"/api/v1/infrastructures/{infra.id}/custom-domains/{domain_id}/verify/")
    force_authenticate(request, user=SimpleNamespace(id=stranger.id))

    response = custom_domain_verify(request, str(infra.id), str(domain_id))

    assert response.status_code == 404


def test_delete_pending_returns_disabled(factory, make_infra):
    infra = make_infra()
    claim_response = _claim(factory, infra)
    domain_id = claim_response.data["id"]

    request = factory.delete(f"/api/v1/infrastructures/{infra.id}/custom-domains/{domain_id}/")
    force_authenticate(request, user=SimpleNamespace(id=infra.user_id))

    with patch("api.services.custom_domain_service.CustomDomainService._detach", return_value=True):
        response = custom_domain_detail(request, str(infra.id), str(domain_id))

    assert response.status_code == 200
    assert response.data["status"] == "DISABLED"

    from api.models.custom_domain import CustomDomain
    domain = CustomDomain.objects.get(id=domain_id)
    assert domain.status == "DISABLED"
    assert domain.cert_arn is None


def test_delete_nonexistent_domain_returns_404(factory, make_infra):
    infra = make_infra()
    request = factory.delete(f"/api/v1/infrastructures/{infra.id}/custom-domains/{uuid.uuid4()}/")
    force_authenticate(request, user=SimpleNamespace(id=infra.user_id))

    response = custom_domain_detail(request, str(infra.id), str(uuid.uuid4()))

    assert response.status_code == 404


def test_budget_exceeded_returns_429(factory, make_infra, monkeypatch):
    infra = make_infra()
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: 30)

    request = factory.get(f"/api/v1/infrastructures/{infra.id}/custom-domains/")
    force_authenticate(request, user=SimpleNamespace(id=infra.user_id))

    response = custom_domain_list_create(request, str(infra.id))

    assert response.status_code == 429
    assert response["Retry-After"] == "30"
