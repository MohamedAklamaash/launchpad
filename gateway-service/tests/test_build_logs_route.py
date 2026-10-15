"""F7 ticket 8 build-logs routes at the gateway: each call costs an AssumeRole plus up to
two CloudWatch GetLogEvents calls, so — like GET /logs for runtime logs — these routes
must never be rate-limit exempt. The gateway also never verifies JWTs, so exemption is
decided before anything authenticates."""
from constants import is_rate_limit_exempt


def test_get_build_logs_is_not_exempt():
    assert is_rate_limit_exempt(
        "GET",
        "/api/applications/018e1234-abcd-7000-8000-000000000001/deployments/"
        "018e1234-abcd-7000-8000-000000000002/build-logs",
    ) is False


def test_get_current_build_logs_is_not_exempt():
    assert is_rate_limit_exempt(
        "GET", "/api/applications/018e1234-abcd-7000-8000-000000000001/build-logs/current",
    ) is False


def test_malformed_app_id_is_rejected_before_reaching_proxy_request(monkeypatch):
    """app_id/deployment_id are typed as uuid.UUID (not str) on this route: a raw str
    would let a malformed id (e.g. path/percent-encoding confusion) reach the f-string
    upstream URL proxy_request builds."""
    from app.api.endpoints import application as application_module
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.responses import Response

    called = {}

    async def _fake_proxy(url, request):
        called["url"] = url
        return Response(content=b"{}", status_code=200)

    monkeypatch.setattr(application_module, "proxy_request", _fake_proxy)

    app = FastAPI()
    app.include_router(application_module.router)
    client = TestClient(app)

    resp = client.get("/applications/not-a-uuid/deployments/018e1234-abcd-7000-8000-000000000002/build-logs")
    assert resp.status_code == 422
    assert "url" not in called

    resp = client.get("/applications/018e1234-abcd-7000-8000-000000000001/deployments/not-a-uuid/build-logs")
    assert resp.status_code == 422
    assert "url" not in called

    resp = client.get("/applications/not-a-uuid/build-logs/current")
    assert resp.status_code == 422
    assert "url" not in called


def test_build_logs_route_proxies_to_application_service(monkeypatch):
    from app.api.endpoints import application as application_module
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.responses import Response

    called = {}

    async def _fake_proxy(url, request):
        called["url"] = url
        return Response(content=b'{"events": [], "next_cursor": null, "complete": true}', status_code=200)

    monkeypatch.setattr(application_module, "proxy_request", _fake_proxy)

    app = FastAPI()
    app.include_router(application_module.router)
    client = TestClient(app)

    app_id = "018e1234-abcd-7000-8000-000000000001"
    deployment_id = "018e1234-abcd-7000-8000-000000000002"

    resp = client.get(f"/applications/{app_id}/deployments/{deployment_id}/build-logs")
    assert resp.status_code == 200
    assert called["url"].endswith(f"/api/v1/applications/{app_id}/deployments/{deployment_id}/build-logs/")

    resp = client.get(f"/applications/{app_id}/build-logs/current")
    assert resp.status_code == 200
    assert called["url"].endswith(f"/api/v1/applications/{app_id}/build-logs/current/")
