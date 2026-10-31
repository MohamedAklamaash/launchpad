"""F9 ticket 9: gateway routes for organization and member management, proxied to
auth-service with Authorization passed through. Every path id is a UUID and every body is
validated here before anything is forwarded."""
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
    seen = {}

    async def _fake(url, request):
        from starlette.responses import JSONResponse
        seen["url"] = url
        seen["method"] = request.method
        seen["authorization"] = request.headers.get("authorization")
        seen["calls"] = seen.get("calls", 0) + 1
        return JSONResponse({"ok": True})

    monkeypatch.setattr("app.api.endpoints.organization.proxy_request", _fake)
    return seen


def _client():
    from app.api.router import api_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


def _h():
    return {"Authorization": BEARER}


def test_list_organizations_proxies_to_the_collection_without_a_redirect(captured):
    resp = _client().get("/organizations", headers=_h(), follow_redirects=False)

    assert resp.status_code == 200
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/api/v1/organizations")
    assert captured["authorization"] == BEARER


def test_create_organization_proxies_a_post_with_authorization_unchanged(captured):
    resp = _client().post("/organizations", json={"name": "Acme"}, headers=_h(),
                          follow_redirects=False)

    assert resp.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/organizations")
    assert captured["authorization"] == BEARER


@pytest.mark.parametrize("body", [
    {},
    {"name": ""},
    {"name": "x" * 201},
    {"name": 7},
    {"name": "Acme", "id": ORG_ID},
    {"name": "Acme", "organization_id": ORG_ID},
    {"name": "Acme", "owner_account_id": ACCOUNT_ID},
])
def test_create_and_rename_reject_malformed_bodies_and_client_chosen_ids_before_proxying(
        captured, body):
    client = _client()

    created = client.post("/organizations", json=body, headers=_h())
    renamed = client.patch(f"/organizations/{ORG_ID}", json=body, headers=_h())

    assert created.status_code == renamed.status_code == 422
    assert "calls" not in captured


def test_rename_proxies_a_patch_to_the_organization(captured):
    resp = _client().patch(f"/organizations/{ORG_ID}", json={"name": "New"}, headers=_h())

    assert resp.status_code == 200
    assert captured["method"] == "PATCH"
    assert captured["url"].endswith(f"/api/v1/organizations/{ORG_ID}")
    assert captured["authorization"] == BEARER


def test_organizations_have_no_delete_route(captured):
    resp = _client().delete(f"/organizations/{ORG_ID}", headers=_h())

    assert resp.status_code in (404, 405)
    assert "calls" not in captured


def test_member_routes_proxy_to_the_matching_upstream_routes(captured):
    client = _client()
    member_path = f"/api/v1/organizations/{ORG_ID}/members/{ACCOUNT_ID}"

    client.get(f"/organizations/{ORG_ID}/members", headers=_h())
    assert captured["method"] == "GET"
    assert captured["url"].endswith(f"/api/v1/organizations/{ORG_ID}/members")

    client.patch(f"/organizations/{ORG_ID}/members/{ACCOUNT_ID}", json={"role": "viewer"},
                 headers=_h())
    assert captured["method"] == "PATCH"
    assert captured["url"].endswith(member_path)

    client.delete(f"/organizations/{ORG_ID}/members/{ACCOUNT_ID}", headers=_h())
    assert captured["method"] == "DELETE"
    assert captured["url"].endswith(member_path)
    assert captured["authorization"] == BEARER
    assert captured["calls"] == 3


@pytest.mark.parametrize("body", [
    {"role": "OWNER"},
    {"role": "superuser"},
    {},
    {"role": "admin", "organization_id": ORG_ID},
])
def test_change_role_rejects_malformed_bodies_before_proxying(captured, body):
    resp = _client().patch(f"/organizations/{ORG_ID}/members/{ACCOUNT_ID}", json=body,
                           headers=_h())

    assert resp.status_code == 422
    assert "calls" not in captured


def test_change_role_forwards_owner_so_auth_service_can_refuse_and_report_it(captured):
    resp = _client().patch(f"/organizations/{ORG_ID}/members/{ACCOUNT_ID}",
                           json={"role": "owner"}, headers=_h())

    assert resp.status_code == 200
    assert captured["calls"] == 1


@pytest.mark.parametrize("method,path", [
    ("GET", "/organizations/not-a-uuid/members"),
    ("PATCH", f"/organizations/{ORG_ID}/members/not-a-uuid"),
    ("DELETE", f"/organizations/{ORG_ID}/members/not-a-uuid"),
    ("DELETE", f"/organizations/{ORG_ID}/members/{ACCOUNT_ID}%2F..%2Fx"),
    ("DELETE", f"/organizations/{ORG_ID}/members/{ACCOUNT_ID}%3Fx=1"),
    ("PATCH", "/organizations/not-a-uuid"),
])
def test_member_path_ids_must_be_uuids(captured, method, path):
    resp = _client().request(method, path, json={"role": "viewer", "name": "x"}, headers=_h())

    assert resp.status_code in (404, 422)
    assert "calls" not in captured


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/organizations"),
    ("POST", "/api/organizations"),
    ("PATCH", f"/api/organizations/{ORG_ID}"),
    ("GET", f"/api/organizations/{ORG_ID}/members"),
    ("PATCH", f"/api/organizations/{ORG_ID}/members/{ACCOUNT_ID}"),
    ("DELETE", f"/api/organizations/{ORG_ID}/members/{ACCOUNT_ID}"),
])
def test_org_management_routes_are_not_rate_limit_exempt(method, path):
    from constants import is_rate_limit_exempt

    assert not is_rate_limit_exempt(method, path)
