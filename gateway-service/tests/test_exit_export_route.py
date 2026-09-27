"""The exit-export and complete-exit routes path-validate infra_id as a UUID at the
gateway, and neither is exempt from the per-IP rate limiter — both cost the customer an
internal call (export-inventory) or a platform-DNS write (complete-exit), same class of
endpoint as evidence-pack and costs."""
import os

import pytest
from constants import is_rate_limit_exempt


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


def test_get_exit_export_is_not_rate_limit_exempt():
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123/exit-export") is False
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123/exit-export/") is False


def test_post_complete_exit_is_not_rate_limit_exempt():
    assert is_rate_limit_exempt("POST", "/api/infrastructures/abc-123/exit") is False
    assert is_rate_limit_exempt("POST", "/api/infrastructures/abc-123/exit/") is False


def test_malformed_infra_id_rejected_before_proxying_exit_export(monkeypatch):
    from app.services import proxy

    called = []

    async def _fake_proxy_request(url, request):
        called.append(url)
        raise AssertionError("must not proxy a malformed infra_id")

    monkeypatch.setattr(proxy, "proxy_request", _fake_proxy_request)
    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().get("/infrastructures/not-a-uuid/exit-export")

    assert resp.status_code == 422
    assert called == []


def test_well_formed_infra_id_is_proxied_for_exit_export(monkeypatch):
    async def _fake_proxy_request(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True, "url": url})

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().get("/infrastructures/018e1234-abcd-7000-8000-000000000001/exit-export")

    assert resp.status_code == 200
    assert resp.json()["url"].endswith(
        "/infrastructures/018e1234-abcd-7000-8000-000000000001/exit-export/"
    )


def test_malformed_infra_id_rejected_before_proxying_complete_exit(monkeypatch):
    from app.services import proxy

    called = []

    async def _fake_proxy_request(url, request):
        called.append(url)
        raise AssertionError("must not proxy a malformed infra_id")

    monkeypatch.setattr(proxy, "proxy_request", _fake_proxy_request)
    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().post("/infrastructures/not-a-uuid/exit", json={"confirm": True})

    assert resp.status_code == 422
    assert called == []


def test_well_formed_complete_exit_is_proxied(monkeypatch):
    async def _fake_proxy_request(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True, "url": url})

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().post(
        "/infrastructures/018e1234-abcd-7000-8000-000000000001/exit", json={"confirm": True}
    )

    assert resp.status_code == 200
    assert resp.json()["url"].endswith(
        "/infrastructures/018e1234-abcd-7000-8000-000000000001/exit/"
    )


def test_complete_exit_requires_confirm_field(monkeypatch):
    async def _fake_proxy_request(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy_request)

    resp = _client().post("/infrastructures/018e1234-abcd-7000-8000-000000000001/exit", json={})
    assert resp.status_code == 422
