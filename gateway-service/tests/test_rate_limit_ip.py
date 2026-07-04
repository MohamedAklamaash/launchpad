from starlette.requests import Request

from app.core import rate_limiter
from app.core.config import settings


def _request(peer, xff=None):
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    scope = {
        "type": "http",
        "method": "GET",
        "headers": headers,
        "query_string": b"",
        "client": (peer, 1234),
        "scheme": "http",
        "server": ("gw", 8000),
        "path": "/api/x",
    }
    return Request(scope)


def test_uses_peer_ip_when_no_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 0)
    req = _request("10.0.0.5", xff="203.0.113.9, 10.0.0.5")
    assert rate_limiter._client_ip(req) == "10.0.0.5"


def test_reads_client_from_xff_behind_one_proxy(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
    req = _request("10.0.0.5", xff="203.0.113.9")
    assert rate_limiter._client_ip(req) == "203.0.113.9"


def test_falls_back_to_peer_when_xff_shorter_than_hops(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 2)
    req = _request("10.0.0.5", xff="203.0.113.9")
    assert rate_limiter._client_ip(req) == "10.0.0.5"
