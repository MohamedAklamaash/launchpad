"""Regression guard for H5: every path parameter on the gateway is either typed
uuid.UUID (rejecting a malformed id with a 422 before proxy_request ever fires) or
carries an explicit character-class Path(pattern=...) constraint. A bare `str` path
param would let a decoded '%2F', '%3F', '%23', or '..' change the upstream request
line — see app/api/endpoints/user.py and notification.py for the two ids that are
not UUID-keyed upstream and are pattern-constrained instead.

This test walks the live route table so a future route with a raw `str` id param
fails CI rather than silently reopening the confused-deputy hole.
"""
import os
import re
import uuid

import pytest

# Characters a confused-deputy id must never be able to smuggle into the upstream
# request line once it's inside a validated path segment.
DANGEROUS_CHARS = "#?/%."


@pytest.fixture(autouse=True)
def _service_urls(monkeypatch):
    for name in (
        "AUTH_SERVICE_URL", "USER_SERVICE_URL", "NOTIFICATION_SERVICE_URL",
        "INFRASTRUCTURE_SERVICE_URL", "APPLICATION_SERVICE_URL", "PAYMENT_SERVICE_URL",
    ):
        monkeypatch.setenv(name, os.environ.get(name, "http://svc:8000"))


def _all_path_params():
    from app.api.router import api_router

    for route in api_router.routes:
        for param in route.dependant.path_params:
            yield route.path, param


def _pattern_constraint(param) -> str | None:
    metadata = getattr(param.field_info, "metadata", [])
    for item in metadata:
        pattern = getattr(item, "pattern", None)
        if pattern:
            return pattern
    return None


def test_every_path_param_is_uuid_or_pattern_constrained():
    unconstrained = [
        f"{path}:{param.name}"
        for path, param in _all_path_params()
        if param.type_ is not uuid.UUID and _pattern_constraint(param) is None
    ]
    assert unconstrained == [], (
        "path params must be typed uuid.UUID or carry Path(pattern=...): "
        f"{unconstrained}"
    )


def test_pattern_constraints_actually_reject_dangerous_characters():
    """A pattern like `.*` would satisfy the check above without providing any real
    protection. Every pattern-constrained param's regex must reject each dangerous
    character embedded in an otherwise-plausible id."""
    weak = []
    for path, param in _all_path_params():
        pattern = _pattern_constraint(param)
        if pattern is None:
            continue
        for char in DANGEROUS_CHARS:
            if re.fullmatch(pattern, f"a{char}b"):
                weak.append(f"{path}:{param.name} pattern {pattern!r} allows {char!r}")
    assert weak == []


REPRESENTATIVE_ROUTES = [
    ("get", "/infrastructures/{value}"),
    ("get", "/applications/{value}"),
    ("get", "/infrastructures/018e1234-abcd-7000-8000-000000000001/databases/{value}"),
]


def _client():
    from app.api.router import api_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


@pytest.mark.parametrize("method,template", REPRESENTATIVE_ROUTES)
# Literal ".." is normalized away by the HTTP client itself (RFC 3986 dot-segment
# removal) before the request is even sent, same as any browser or curl would do —
# it never reaches our routing. "%2e%2e" is the encoded form an attacker actually
# has to send to keep the dot-segment intact until the server decodes it.
@pytest.mark.parametrize("bad_value", ["%23", "%3F", "%2F", "%2e%2e", "not-a-uuid"])
def test_malformed_uuid_path_param_rejected_on_representative_routes(monkeypatch, method, template, bad_value):
    from app.services import proxy

    async def _fail(url, request):
        raise AssertionError(f"must not proxy a malformed id, got url={url}")

    monkeypatch.setattr(proxy, "proxy_request", _fail)
    for mod_name in ("infrastructure", "application", "database"):
        monkeypatch.setattr(f"app.api.endpoints.{mod_name}.proxy_request", _fail, raising=False)

    client = _client()
    resp = getattr(client, method)(template.format(value=bad_value))
    # 422: FastAPI's own uuid.UUID validation rejected it before proxy_request ran.
    # 404: Starlette's router normalized/decoded '..' or '%2F' before matching and
    # never routed the request to this endpoint at all. Either way proxy_request
    # (asserted to raise above) never fires.
    assert resp.status_code in (404, 422)


def test_well_formed_uuid_path_params_still_proxy(monkeypatch):
    calls = []

    async def _fake_proxy(url, request):
        calls.append(url)
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    monkeypatch.setattr("app.api.endpoints.infrastructure.proxy_request", _fake_proxy)

    client = _client()
    infra_id = uuid.uuid4()
    resp = client.get(f"/infrastructures/{infra_id}")

    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0].endswith(f"/api/v1/infrastructures/{infra_id}/")


@pytest.mark.parametrize("bad_value", ["%23", "%3F", "..%2F.."])
def test_non_uuid_user_id_still_rejects_path_breaking_characters(monkeypatch, bad_value):
    from app.services import proxy

    async def _fail(url, request):
        raise AssertionError(f"must not proxy a path-breaking user id, got url={url}")

    monkeypatch.setattr(proxy, "proxy_request", _fail)
    monkeypatch.setattr("app.api.endpoints.user.proxy_request", _fail, raising=False)
    monkeypatch.setattr("app.api.endpoints.notification.proxy_request", _fail, raising=False)

    client = _client()
    assert client.get(f"/users/{bad_value}").status_code in (404, 422)
    assert client.get(f"/notifications/user/{bad_value}").status_code in (404, 422)


def test_well_formed_non_uuid_user_id_still_proxies(monkeypatch):
    calls = []

    async def _fake_proxy(url, request):
        calls.append(url)
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    monkeypatch.setattr("app.api.endpoints.user.proxy_request", _fake_proxy)

    client = _client()
    resp = client.get("/users/user_abc-123")

    assert resp.status_code == 200
    assert calls[0].endswith("/api/v1/users/user_abc-123")
