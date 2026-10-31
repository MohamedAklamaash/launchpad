"""F9 ticket 11: gateway route for the organization audit log. The path id is a UUID and every
query parameter is typed here before anything is forwarded; auth-service owns authorization."""
import os

import pytest

ORG_ID = "11111111-1111-4111-8111-111111111111"
ACCOUNT_ID = "33333333-3333-4333-8333-333333333333"
BEARER = "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig"


@pytest.fixture(autouse=True)
def _service_urls(monkeypatch):
    for name in (
        "AUTH_SERVICE_URL", "USER_SERVICE_URL", "NOTIFICATION_SERVICE_URL",
        "INFRASTRUCTURE_SERVICE_URL", "APPLICATION_SERVICE_URL", "PAYMENT_SERVICE_URL",
    ):
        monkeypatch.setenv(name, os.environ.get(name, "http://svc:8000"))


@pytest.fixture
def captured(monkeypatch):
    seen = {"calls": 0}

    async def _fake(url, request):
        from starlette.responses import JSONResponse
        seen["url"] = url
        seen["query"] = dict(request.query_params)
        seen["authorization"] = request.headers.get("authorization")
        seen["calls"] += 1
        return JSONResponse({"events": [], "next_cursor": None})

    monkeypatch.setattr("app.api.endpoints.organization.proxy_request", _fake)
    return seen


def _client():
    from app.api.router import api_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


def _get(query=None, org=ORG_ID):
    return _client().get(
        f"/organizations/{org}/audit-events", params=query, headers={"Authorization": BEARER}
    )


def test_audit_events_proxies_with_every_filter_and_the_bearer(captured):
    query = {
        "limit": "25",
        "cursor": "MTc5MzQ2OTU1MDYzMS5hYmM",
        "actor_account_id": ACCOUNT_ID,
        "target_account_id": ACCOUNT_ID,
        "action": "membership.role_changed",
        "from": "2026-01-01T00:00:00Z",
        "to": "2026-02-01T00:00:00Z",
    }

    resp = _get(query)

    assert resp.status_code == 200
    assert captured["url"].endswith(f"/api/v1/organizations/{ORG_ID}/audit-events")
    assert captured["query"] == query
    assert captured["authorization"] == BEARER


def test_audit_events_without_filters_forwards_no_query(captured):
    assert _get().status_code == 200
    assert captured["query"] == {}


@pytest.mark.parametrize("query", [
    {"limit": "0"},
    {"limit": "101"},
    {"limit": "abc"},
    {"cursor": "has space"},
    {"cursor": "a" * 101},
    {"actor_account_id": "not-a-uuid"},
    {"target_account_id": "1"},
    {"action": "membership.exploded"},
    {"from": "yesterday"},
    {"to": "also-not-a-date"},
])
def test_audit_events_rejects_bad_query_values_before_proxying(captured, query):
    resp = _get(query)

    assert resp.status_code == 422
    assert captured["calls"] == 0


def test_audit_events_org_id_must_be_a_uuid(captured):
    resp = _get(org="not-a-uuid")

    assert resp.status_code == 422
    assert captured["calls"] == 0
