"""Per-app metrics at the gateway: each cache-miss call costs an AssumeRole plus a
CloudWatch call in the customer's own account, so — like GET /logs — this route must
never be rate-limit exempt. The gateway also never verifies JWTs, so exemption is decided
before anything authenticates."""
from constants import is_rate_limit_exempt


def test_get_app_metrics_is_not_exempt():
    assert is_rate_limit_exempt("GET", "/api/applications/018e1234-abcd-7000-8000-000000000001/metrics") is False
    assert is_rate_limit_exempt("GET", "/api/applications/018e1234-abcd-7000-8000-000000000001/metrics/") is False


def test_malformed_app_id_is_rejected_before_reaching_proxy_request(monkeypatch):
    """app_id is typed as uuid.UUID (not str) on this route: a raw str would let a
    malformed id (e.g. path/percent-encoding confusion) reach the f-string upstream URL
    proxy_request builds."""
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

    resp = client.get("/applications/not-a-uuid/metrics")

    assert resp.status_code == 422
    assert "url" not in called


def test_valid_app_id_and_range_query_param_pass_through(monkeypatch):
    from app.api.endpoints import application as application_module
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.responses import Response

    called = {}

    async def _fake_proxy(url, request):
        called["url"] = url
        called["range"] = request.query_params.get("range")
        return Response(content=b"{}", status_code=200)

    monkeypatch.setattr(application_module, "proxy_request", _fake_proxy)

    app = FastAPI()
    app.include_router(application_module.router)
    client = TestClient(app)

    app_id = "018e1234-abcd-7000-8000-000000000001"
    resp = client.get(f"/applications/{app_id}/metrics", params={"range": "6h"})

    assert resp.status_code == 200
    assert called["url"].endswith(f"/api/v1/applications/{app_id}/metrics/")
    assert called["range"] == "6h"
