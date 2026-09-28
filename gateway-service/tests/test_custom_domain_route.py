"""Custom-domain gateway routes: typed UUID path params reject a malformed id before
proxying (rather than forwarding it to infrastructure-service on every request), and a
well-formed request proxies to the expected infrastructure-service URL."""
import os

import pytest


@pytest.fixture(autouse=True)
def _service_urls(monkeypatch):
    for name in (
        "AUTH_SERVICE_URL", "USER_SERVICE_URL", "NOTIFICATION_SERVICE_URL",
        "INFRASTRUCTURE_SERVICE_URL", "APPLICATION_SERVICE_URL", "PAYMENT_SERVICE_URL",
    ):
        monkeypatch.setenv(name, os.environ.get(name, "http://svc:8000"))


def _client():
    from app.api.router import api_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


INFRA_ID = "018e1234-abcd-7000-8000-000000000001"
DOMAIN_ID = "018e1234-abcd-7000-8000-000000000002"
APP_ID = "018e1234-abcd-7000-8000-000000000003"


def _fake_proxy_request_factory(called):
    async def _fake(url, request):
        from starlette.responses import JSONResponse
        called.append(url)
        return JSONResponse({"ok": True, "url": url})

    return _fake


def test_malformed_infra_id_rejected_before_proxying_on_list(monkeypatch):
    called = []
    monkeypatch.setattr("app.api.endpoints.custom_domain.proxy_request", _fake_proxy_request_factory(called))

    resp = _client().get("/infrastructures/not-a-uuid/custom-domains/")

    assert resp.status_code == 422
    assert called == []


def test_malformed_domain_id_rejected_before_proxying_on_verify(monkeypatch):
    called = []
    monkeypatch.setattr("app.api.endpoints.custom_domain.proxy_request", _fake_proxy_request_factory(called))

    resp = _client().post(f"/infrastructures/{INFRA_ID}/custom-domains/not-a-uuid/verify")

    assert resp.status_code == 422
    assert called == []


def test_list_proxies_to_infrastructure_service(monkeypatch):
    called = []
    monkeypatch.setattr("app.api.endpoints.custom_domain.proxy_request", _fake_proxy_request_factory(called))

    resp = _client().get(f"/infrastructures/{INFRA_ID}/custom-domains/")

    assert resp.status_code == 200
    assert called[0].endswith(f"/infrastructures/{INFRA_ID}/custom-domains/")


def test_claim_rejects_body_missing_application_id(monkeypatch):
    called = []
    monkeypatch.setattr("app.api.endpoints.custom_domain.proxy_request", _fake_proxy_request_factory(called))

    resp = _client().post(f"/infrastructures/{INFRA_ID}/custom-domains/", json={"hostname": "app.example.com"})

    assert resp.status_code == 422
    assert called == []


def test_claim_proxies_to_infrastructure_service(monkeypatch):
    called = []
    monkeypatch.setattr("app.api.endpoints.custom_domain.proxy_request", _fake_proxy_request_factory(called))

    resp = _client().post(
        f"/infrastructures/{INFRA_ID}/custom-domains/",
        json={"application_id": APP_ID, "hostname": "app.example.com"},
    )

    assert resp.status_code == 200
    assert called[0].endswith(f"/infrastructures/{INFRA_ID}/custom-domains/")


def test_verify_proxies_to_infrastructure_service(monkeypatch):
    called = []
    monkeypatch.setattr("app.api.endpoints.custom_domain.proxy_request", _fake_proxy_request_factory(called))

    resp = _client().post(f"/infrastructures/{INFRA_ID}/custom-domains/{DOMAIN_ID}/verify")

    assert resp.status_code == 200
    assert called[0].endswith(f"/infrastructures/{INFRA_ID}/custom-domains/{DOMAIN_ID}/verify/")


def test_delete_proxies_to_infrastructure_service(monkeypatch):
    called = []
    monkeypatch.setattr("app.api.endpoints.custom_domain.proxy_request", _fake_proxy_request_factory(called))

    resp = _client().delete(f"/infrastructures/{INFRA_ID}/custom-domains/{DOMAIN_ID}")

    assert resp.status_code == 200
    assert called[0].endswith(f"/infrastructures/{INFRA_ID}/custom-domains/{DOMAIN_ID}/")
