"""F2 runtime logs at the gateway: each call costs an AssumeRole plus a CloudWatch/k8s API
call in the customer's own account, so — like GET /logs for provisioning — this route must
never be rate-limit exempt. The gateway also never verifies JWTs, so exemption is decided
before anything authenticates."""
from constants import is_rate_limit_exempt


def test_get_runtime_logs_is_not_exempt():
    assert is_rate_limit_exempt("GET", "/api/applications/018e1234-abcd-7000-8000-000000000001/logs") is False
    assert is_rate_limit_exempt("GET", "/api/applications/018e1234-abcd-7000-8000-000000000001/logs/") is False


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

    resp = client.get("/applications/not-a-uuid/logs")

    assert resp.status_code == 422
    assert "url" not in called
