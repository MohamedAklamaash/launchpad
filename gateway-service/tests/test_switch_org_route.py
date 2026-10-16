"""F9 ticket 5: gateway route for POST /auth/switch-org, proxied the same way every
other /auth/* route is (gateway never verifies JWTs — it just forwards Authorization
through, per CLAUDE.md's Auth model).
"""
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


def test_switch_org_proxies_to_the_auth_service_upstream_route(monkeypatch):
    captured = {}

    async def _fake(url, request):
        from starlette.responses import JSONResponse
        captured["url"] = url
        captured["method"] = request.method
        captured["authorization"] = request.headers.get("authorization")
        return JSONResponse({"accessToken": "x", "refreshToken": "y"})

    monkeypatch.setattr("app.api.endpoints.auth.proxy_request", _fake)

    resp = _client().post(
        "/auth/switch-org",
        json={
            "organization_id": "11111111-1111-1111-1111-111111111111",
            "refresh_token": "eyJhbGciOiJIUzI1NiJ9.e30.sig",
        },
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig"},
    )

    assert resp.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/auth/switch-org")
    # The gateway must not verify or strip the Authorization header — it is passed
    # through unchanged for auth-service to verify (CLAUDE.md's Auth model: "Does NOT
    # verify JWTs — passes Authorization through").
    assert captured["authorization"] == "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig"


def test_switch_org_requires_organization_id_and_refresh_token_in_the_body():
    resp = _client().post("/auth/switch-org", json={}, headers={"Authorization": "Bearer x"})

    assert resp.status_code == 422


def test_switch_org_rejects_a_non_uuid_organization_id_at_the_gateway():
    resp = _client().post(
        "/auth/switch-org",
        json={"organization_id": "not-a-uuid", "refresh_token": "tok"},
        headers={"Authorization": "Bearer x"},
    )

    assert resp.status_code == 422
