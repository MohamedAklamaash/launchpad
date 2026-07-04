import asyncio
from unittest.mock import patch

from starlette.requests import Request

from app.services import proxy
from app.core.config import settings


class _FakeResponse:
    status_code = 200
    content = b"ok"
    headers = {}


def _make_request(headers):
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
        "client": ("1.2.3.4", 1234),
        "scheme": "http",
        "server": ("gw", 8000),
        "path": "/api/x",
        "root_path": "",
    }

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request(scope, receive)


def test_gateway_strips_client_supplied_internal_token():
    captured = {}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, **kwargs):
            captured["headers"] = kwargs["headers"]
            return _FakeResponse()

    req = _make_request({"X-INTERNAL-TOKEN": "attacker-supplied", "host": "gw"})
    with patch.object(proxy.httpx, "AsyncClient", lambda: _FakeClient()):
        asyncio.run(proxy.proxy_request("http://svc/api/x", req))

    headers = captured["headers"]
    assert headers.get("X-INTERNAL-TOKEN") == settings.INTERNAL_API_TOKEN
    assert headers.get("x-internal-token") is None
    assert "attacker-supplied" not in headers.values()
