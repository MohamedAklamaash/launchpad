"""SSE build-logs stream routes at the gateway: not rate-limit exempt (same cost
rationale as the JSON build-logs routes, see test_build_logs_route.py), malformed ids
rejected before `proxy_stream_request`, and each route proxies to its own stream path."""
from constants import is_rate_limit_exempt


def test_get_build_logs_stream_is_not_exempt():
    assert is_rate_limit_exempt(
        "GET",
        "/api/applications/018e1234-abcd-7000-8000-000000000001/deployments/"
        "018e1234-abcd-7000-8000-000000000002/build-logs/stream",
    ) is False


def test_get_current_build_logs_stream_is_not_exempt():
    assert is_rate_limit_exempt(
        "GET", "/api/applications/018e1234-abcd-7000-8000-000000000001/build-logs/current/stream",
    ) is False


def test_malformed_ids_are_rejected_before_reaching_proxy_stream_request(monkeypatch):
    from app.api.endpoints import application as application_module
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.responses import Response

    called = {}

    async def _fake_proxy(url, request):
        called["url"] = url
        return Response(content=b"{}", status_code=200)

    monkeypatch.setattr(application_module, "proxy_stream_request", _fake_proxy)

    app = FastAPI()
    app.include_router(application_module.router)
    client = TestClient(app)

    resp = client.get(
        "/applications/not-a-uuid/deployments/018e1234-abcd-7000-8000-000000000002/build-logs/stream",
    )
    assert resp.status_code == 422
    assert "url" not in called

    resp = client.get("/applications/not-a-uuid/build-logs/current/stream")
    assert resp.status_code == 422
    assert "url" not in called


def test_build_logs_stream_routes_proxy_to_their_stream_paths(monkeypatch):
    from app.api.endpoints import application as application_module
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.responses import Response

    called = {}

    async def _fake_proxy(url, request):
        called["url"] = url
        return Response(content=b"ok", status_code=200)

    monkeypatch.setattr(application_module, "proxy_stream_request", _fake_proxy)

    app = FastAPI()
    app.include_router(application_module.router)
    client = TestClient(app)

    app_id = "018e1234-abcd-7000-8000-000000000001"
    deployment_id = "018e1234-abcd-7000-8000-000000000002"

    resp = client.get(f"/applications/{app_id}/deployments/{deployment_id}/build-logs/stream")
    assert resp.status_code == 200
    assert called["url"].endswith(f"/api/v1/applications/{app_id}/deployments/{deployment_id}/build-logs/stream/")

    resp = client.get(f"/applications/{app_id}/build-logs/current/stream")
    assert resp.status_code == 200
    assert called["url"].endswith(f"/api/v1/applications/{app_id}/build-logs/current/stream/")
