"""The dashboard's Nuke infrastructure button needs POST/GET /api/infrastructures/{id}/nuke
routed to infrastructure-service — same proxy pattern as reissue-token/exit."""
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


def test_nuke_start_is_proxied_to_infrastructure_service(monkeypatch):
    async def _fake_proxy_request(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse({"url": url, "method": request.method})

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().post(
        "/infrastructures/018e1234-abcd-7000-8000-000000000001/nuke",
        json={"confirm_name": "prod-infra"},
    )

    assert resp.status_code == 200
    assert resp.json()["method"] == "POST"
    assert resp.json()["url"].endswith("/api/v1/infrastructures/018e1234-abcd-7000-8000-000000000001/nuke/")


def test_nuke_status_is_proxied_to_infrastructure_service(monkeypatch):
    async def _fake_proxy_request(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse({"url": url, "method": request.method})

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().get("/infrastructures/018e1234-abcd-7000-8000-000000000001/nuke")

    assert resp.status_code == 200
    assert resp.json()["method"] == "GET"
    assert resp.json()["url"].endswith("/api/v1/infrastructures/018e1234-abcd-7000-8000-000000000001/nuke/")


def test_nuke_start_rejects_a_malformed_infra_id(monkeypatch):
    async def _fake_proxy_request(url, request):
        raise AssertionError("must not proxy a malformed infra_id")

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().post("/infrastructures/not-a-uuid/nuke", json={"confirm_name": "x"})
    assert resp.status_code == 422


def test_nuke_start_requires_confirm_name(monkeypatch):
    async def _fake_proxy_request(url, request):
        raise AssertionError("must not proxy a missing confirm_name")

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().post("/infrastructures/018e1234-abcd-7000-8000-000000000001/nuke", json={})
    assert resp.status_code == 422
