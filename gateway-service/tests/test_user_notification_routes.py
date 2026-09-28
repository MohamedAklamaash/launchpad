"""The gateway does not verify JWTs (CLAUDE.md) — user-service and notification-service
enforce caller identity themselves. These tests confirm the gateway stays a transparent
proxy for these routes: it never blocks or rewrites an unauthenticated call itself, it
passes a downstream 401/403 straight through, and its documented response models match
what the (now-scoped) downstream endpoints actually return.
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


def _fake_json_response(payload, status_code=200):
    async def _fake(url, request):
        from starlette.responses import JSONResponse
        return JSONResponse(payload, status_code=status_code)

    return _fake


def test_user_search_downstream_401_passes_through_unmodified(monkeypatch):
    monkeypatch.setattr(
        "app.api.endpoints.user.proxy_request",
        _fake_json_response({"message": "Authorization header with Bearer token is required"}, 401),
    )
    resp = _client().get("/users/", params={"q": "john"})
    assert resp.status_code == 401


def test_user_get_downstream_403_passes_through_unmodified(monkeypatch):
    monkeypatch.setattr(
        "app.api.endpoints.user.proxy_request",
        _fake_json_response({"message": "You may only view your own profile"}, 403),
    )
    resp = _client().get("/users/some-other-user-id")
    assert resp.status_code == 403


def test_user_search_forwards_query_and_scoped_minimal_result(monkeypatch):
    captured = {}
    downstream_payload = [
        {"user_id": "u1", "user_name": "john", "email": "john@example.com", "profile_url": None},
    ]

    async def _fake(url, request):
        from starlette.responses import JSONResponse
        captured["url"] = url
        captured["q"] = request.query_params.get("q")
        return JSONResponse(downstream_payload)

    monkeypatch.setattr("app.api.endpoints.user.proxy_request", _fake)

    resp = _client().get("/users/", params={"q": "john"})

    assert resp.status_code == 200
    assert captured["q"] == "john"
    assert captured["url"].endswith("/api/v1/users/")
    # proxy_request returns a raw Response, so FastAPI's response_model is documentation
    # only here (not runtime validation) — the gateway must not alter the payload.
    assert resp.json() == downstream_payload

    # The response_model itself still needs to accept the minimal shape user-service now
    # returns — this is the actual regression check for "gateway models updated to match".
    from app.api.endpoints.user import UserSearchResult

    assert UserSearchResult(**downstream_payload[0]).user_id == "u1"


def test_notification_me_downstream_401_passes_through_unmodified(monkeypatch):
    monkeypatch.setattr(
        "app.api.endpoints.notification.proxy_request",
        _fake_json_response({"message": "Authorization header with Bearer token is required"}, 401),
    )
    resp = _client().get("/notifications/me")
    assert resp.status_code == 401


def test_notification_me_has_no_target_user_path_param(monkeypatch):
    captured = {}

    async def _fake(url, request):
        from starlette.responses import JSONResponse
        captured["url"] = url
        return JSONResponse([])

    monkeypatch.setattr("app.api.endpoints.notification.proxy_request", _fake)

    resp = _client().get("/notifications/me")

    assert resp.status_code == 200
    assert captured["url"].endswith("/api/v1/notifications/me")

    # The old caller-chosen-id route is gone entirely.
    stale = _client().get("/notifications/user/some-user-id")
    assert stale.status_code == 404
