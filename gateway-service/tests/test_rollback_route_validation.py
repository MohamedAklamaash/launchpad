"""The rollback routes' path parameters are typed uuid.UUID so a malformed id is rejected
by FastAPI's own validation (422) before proxy_request ever fires a request at the
upstream service — a bare `str` parameter would forward a value like `#` or `?` straight
through, which could reinterpret the rest of the path or query string at the upstream.
"""
import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _service_urls(monkeypatch):
    for name in (
        "AUTH_SERVICE_URL", "USER_SERVICE_URL", "NOTIFICATION_SERVICE_URL",
        "INFRASTRUCTURE_SERVICE_URL", "APPLICATION_SERVICE_URL", "PAYMENT_SERVICE_URL",
    ):
        monkeypatch.setenv(name, os.environ.get(name, "http://svc:8000"))


def _client():
    from app.api.endpoints import application

    app = FastAPI()
    app.include_router(application.router, prefix="/api")
    return TestClient(app)


@pytest.mark.parametrize("bad_id", ["%23", "%3F", "not-a-uuid"])
def test_malformed_app_id_is_rejected_before_reaching_the_proxy(bad_id):
    client = _client()
    resp = client.get(f"/api/applications/{bad_id}/deployments")
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_id", ["%23", "%3F", "not-a-uuid"])
def test_malformed_deployment_id_is_rejected_before_reaching_the_proxy(bad_id):
    client = _client()
    ok_app_id = uuid.uuid4()
    resp = client.get(f"/api/applications/{ok_app_id}/deployments/{bad_id}/preview")
    assert resp.status_code == 422

    resp = client.post(f"/api/applications/{ok_app_id}/deployments/{bad_id}/rollback")
    assert resp.status_code == 422


def test_malformed_app_id_on_resume_is_rejected():
    client = _client()
    resp = client.post("/api/applications/%23/resume-auto-deploy")
    assert resp.status_code == 422


def test_well_formed_uuids_pass_validation_and_reach_the_proxy(monkeypatch):
    """Confirms the 422s above are about validation, not the routes being broken. Patches
    the endpoint module's own `proxy_request` reference (not settings.APPLICATION_SERVICE_URL,
    which is a module-level singleton other test files may have already imported with a
    different value) so this doesn't depend on test collection order."""
    calls = []

    async def _fake_proxy(url, request):
        calls.append(url)
        return []

    from app.api.endpoints import application
    monkeypatch.setattr(application, "proxy_request", _fake_proxy)

    client = _client()
    app_id = uuid.uuid4()
    resp = client.get(f"/api/applications/{app_id}/deployments")

    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0].endswith(f"/api/v1/applications/{app_id}/deployments/")
