"""SSE runtime-logs stream route at the gateway: not rate-limit exempt (same cost
rationale as GET /logs, see test_runtime_logs_route.py), malformed app_id rejected
before `proxy_stream_request`, and the route proxies to the stream path, not the JSON
one."""
from constants import is_rate_limit_exempt


def test_get_runtime_logs_stream_is_not_exempt():
    assert is_rate_limit_exempt("GET", "/api/applications/018e1234-abcd-7000-8000-000000000001/logs/stream") is False


def test_malformed_app_id_is_rejected_before_reaching_proxy_stream_request(monkeypatch):
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

    resp = client.get("/applications/not-a-uuid/logs/stream")

    assert resp.status_code == 422
    assert "url" not in called


def test_stream_route_proxies_to_the_stream_path_not_the_json_one(monkeypatch):
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
    resp = client.get(f"/applications/{app_id}/logs/stream", params={"container": "app"})

    assert resp.status_code == 200
    assert called["url"].endswith(f"/api/v1/applications/{app_id}/logs/stream/")
