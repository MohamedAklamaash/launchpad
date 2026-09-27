"""The costs route path-validates infra_id as a UUID at the gateway, rather than
forwarding a malformed id to infrastructure-service on every request."""
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


def test_malformed_infra_id_is_rejected_before_proxying(monkeypatch):
    from app.services import proxy

    called = []

    async def _fake_proxy_request(url, request):
        called.append(url)
        raise AssertionError("must not proxy a malformed infra_id")

    monkeypatch.setattr(proxy, "proxy_request", _fake_proxy_request)
    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().get("/infrastructures/not-a-uuid/costs")

    assert resp.status_code == 422
    assert called == []


def test_well_formed_infra_id_is_proxied(monkeypatch):
    async def _fake_proxy_request(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True, "url": url})

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().get("/infrastructures/018e1234-abcd-7000-8000-000000000001/costs")

    assert resp.status_code == 200
    assert resp.json()["url"].endswith(
        "/infrastructures/018e1234-abcd-7000-8000-000000000001/costs/"
    )
