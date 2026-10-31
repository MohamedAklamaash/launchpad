"""F9 ticket 6: gateway routes for organization invitations, proxied to auth-service the
same way every other /auth/* route is (the gateway never verifies JWTs, it passes
Authorization through)."""
import os

import pytest

ORG_ID = "11111111-1111-4111-8111-111111111111"
INVITATION_ID = "22222222-2222-4222-8222-222222222222"
INVITE_TOKEN = "A" * 43
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


def test_create_invitation_proxies_with_authorization_unchanged(captured):
    resp = _client().post(
        f"/organizations/{ORG_ID}/invitations",
        json={"email": "a@example.com", "role": "developer"},
        headers={"Authorization": BEARER},
    )

    assert resp.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith(f"/api/v1/organizations/{ORG_ID}/invitations")
    assert captured["authorization"] == BEARER


def test_list_and_revoke_proxy_to_the_matching_upstream_routes(captured):
    client = _client()

    client.get(f"/organizations/{ORG_ID}/invitations", headers={"Authorization": BEARER})
    assert captured["method"] == "GET"
    assert captured["url"].endswith(f"/api/v1/organizations/{ORG_ID}/invitations")

    client.delete(
        f"/organizations/{ORG_ID}/invitations/{INVITATION_ID}",
        headers={"Authorization": BEARER},
    )
    assert captured["method"] == "DELETE"
    assert captured["url"].endswith(
        f"/api/v1/organizations/{ORG_ID}/invitations/{INVITATION_ID}")


def test_accept_proxies_a_post_with_the_token_in_the_body_only(captured):
    resp = _client().post(
        "/invitations/accept", json={"token": INVITE_TOKEN},
        headers={"Authorization": BEARER},
    )

    assert resp.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/invitations/accept")
    assert INVITE_TOKEN not in captured["url"]
    assert captured["authorization"] == BEARER


def test_accept_has_no_get_form(captured):
    resp = _client().get(f"/invitations/accept?token={INVITE_TOKEN}")

    assert resp.status_code in (404, 405)
    assert "calls" not in captured


@pytest.mark.parametrize("body", [
    {"email": "a@example.com", "role": "superuser"},
    {"email": "a@example.com"},
    {"role": "viewer"},
    {},
])
def test_create_invitation_rejects_malformed_bodies_before_proxying(captured, body):
    resp = _client().post(
        f"/organizations/{ORG_ID}/invitations", json=body, headers={"Authorization": BEARER})

    assert resp.status_code == 422
    assert "calls" not in captured


def test_create_invitation_forwards_owner_so_auth_service_can_refuse_and_report_it(captured):
    resp = _client().post(f"/organizations/{ORG_ID}/invitations",
                          json={"email": "a@example.com", "role": "owner"},
                          headers={"Authorization": BEARER})

    assert resp.status_code < 300
    assert captured["calls"] == 1


@pytest.mark.parametrize("token", ["", "short", "A" * 42, "A" * 44, "A" * 42 + "/", "A" * 42 + "."])
def test_accept_rejects_a_malformed_token_before_proxying(captured, token):
    resp = _client().post(
        "/invitations/accept", json={"token": token}, headers={"Authorization": BEARER})

    assert resp.status_code == 422
    assert "calls" not in captured


@pytest.mark.parametrize("path", [
    "/organizations/not-a-uuid/invitations",
    f"/organizations/{ORG_ID}%2F..%2Fadmin/invitations",
    f"/organizations/{ORG_ID}/invitations/not-a-uuid",
    f"/organizations/{ORG_ID}/invitations/{INVITATION_ID}%3Fx=1",
])
def test_path_ids_must_be_uuids(captured, path):
    client = _client()
    resp = client.get(path, headers={"Authorization": BEARER})
    if path.rsplit("/", 1)[-1] != "invitations":
        resp = client.delete(path, headers={"Authorization": BEARER})

    assert resp.status_code in (404, 422)
    assert "calls" not in captured


def test_transfer_ownership_proxies_a_post_with_authorization_unchanged(captured):
    resp = _client().post(
        f"/organizations/{ORG_ID}/transfer-ownership",
        json={"new_owner_account_id": INVITATION_ID},
        headers={"Authorization": BEARER},
    )

    assert resp.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"].endswith(f"/api/v1/organizations/{ORG_ID}/transfer-ownership")
    assert captured["authorization"] == BEARER


def test_transfer_ownership_rejects_non_uuid_path_and_body_before_proxying(captured):
    client = _client()

    bad_path = client.post(
        "/organizations/not-a-uuid/transfer-ownership",
        json={"new_owner_account_id": INVITATION_ID},
    )
    bad_body = client.post(
        f"/organizations/{ORG_ID}/transfer-ownership", json={"new_owner_account_id": "x"},
    )
    missing_body = client.post(f"/organizations/{ORG_ID}/transfer-ownership", json={})

    assert bad_path.status_code == bad_body.status_code == missing_body.status_code == 422
    assert captured.get("calls") is None


def test_transfer_ownership_is_not_rate_limit_exempt():
    from constants import is_rate_limit_exempt

    assert not is_rate_limit_exempt("POST", f"/api/organizations/{ORG_ID}/transfer-ownership")
