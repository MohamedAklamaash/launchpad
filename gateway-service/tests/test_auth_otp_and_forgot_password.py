"""H3 critical follow-up: /auth/forgot-password no longer echoes the OTP or reveals
account existence, and /auth/authenticate-with-otp has a POST variant so a manual OTP
submission doesn't put the code in the URL. These tests are gateway-shape checks only —
the gateway still just proxies; the actual enforcement is in auth-service.
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


def test_forgot_password_response_model_has_no_otp_field():
    from app.api.endpoints.auth import ForgotPasswordResponse

    assert "otp" not in ForgotPasswordResponse.model_fields
    assert "message" in ForgotPasswordResponse.model_fields


def test_forgot_password_proxies_and_never_leaks_an_otp_field(monkeypatch):
    async def _fake(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse(
            {"message": "If that email is registered, a verification code has been sent to it."},
            status_code=202,
        )

    monkeypatch.setattr("app.api.endpoints.auth.proxy_request", _fake)

    resp = _client().post("/auth/forgot-password", json={"email": "anyone@example.com"})

    assert resp.status_code == 202
    assert "otp" not in resp.json()


def test_authenticate_with_otp_post_variant_forwards_to_the_same_upstream_route(monkeypatch):
    captured = {}

    async def _fake(url, request):
        from starlette.responses import JSONResponse
        captured["url"] = url
        captured["method"] = request.method
        return JSONResponse({"accessToken": "x", "refreshToken": "y"})

    monkeypatch.setattr("app.api.endpoints.auth.proxy_request", _fake)

    resp = _client().post(
        "/auth/authenticate-with-otp", json={"email": "user@example.com", "otp": "123456"}
    )

    assert resp.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/auth/authenticate-with-otp")


def test_authenticate_with_otp_get_variant_still_works_for_the_email_magic_link(monkeypatch):
    captured = {}

    async def _fake(url, request):
        from starlette.responses import JSONResponse
        captured["url"] = url
        return JSONResponse({"accessToken": "x", "refreshToken": "y"})

    monkeypatch.setattr("app.api.endpoints.auth.proxy_request", _fake)

    resp = _client().get(
        "/auth/authenticate-with-otp", params={"email": "user@example.com", "otp": "123456"}
    )

    assert resp.status_code == 200
    assert captured["url"].endswith("/api/v1/auth/authenticate-with-otp")


def test_update_password_body_no_longer_accepts_email():
    from app.api.endpoints.auth import UpdatePasswordBody

    assert "email" not in UpdatePasswordBody.model_fields
