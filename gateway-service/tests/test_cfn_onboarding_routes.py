"""F8 ticket 3: gateway routes for the new cfn-verify / cfn-refresh endpoints. The
gateway is a pure proxy here — ownership, the feature flag, and AWS calls all live in
infrastructure-service; these tests only confirm the gateway forwards to the right
trailing-slash Django path with the right method."""
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


def test_cfn_verify_is_proxied_to_infrastructure_service(monkeypatch):
    async def _fake_proxy_request(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse({"url": url, "method": request.method})

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().post("/infrastructures/018e1234-abcd-7000-8000-000000000001/cfn-verify")

    assert resp.status_code == 200
    assert resp.json()["method"] == "POST"
    assert resp.json()["url"].endswith(
        "/api/v1/infrastructures/018e1234-abcd-7000-8000-000000000001/cfn-verify/"
    )


def test_cfn_verify_rejects_a_malformed_infra_id(monkeypatch):
    async def _fake_proxy_request(url, request):
        raise AssertionError("must not proxy a malformed infra_id")

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    assert _client().post("/infrastructures/not-a-uuid/cfn-verify").status_code == 422


def test_cfn_refresh_is_proxied_to_infrastructure_service(monkeypatch):
    async def _fake_proxy_request(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse({"url": url, "method": request.method})

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().post("/infrastructures/018e1234-abcd-7000-8000-000000000001/cfn-refresh")

    assert resp.status_code == 200
    assert resp.json()["method"] == "POST"
    assert resp.json()["url"].endswith(
        "/api/v1/infrastructures/018e1234-abcd-7000-8000-000000000001/cfn-refresh/"
    )


def test_cfn_refresh_rejects_a_malformed_infra_id(monkeypatch):
    async def _fake_proxy_request(url, request):
        raise AssertionError("must not proxy a malformed infra_id")

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    assert _client().post("/infrastructures/not-a-uuid/cfn-refresh").status_code == 422
