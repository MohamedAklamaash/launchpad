"""FastAPI matches routes in registration order, so /infrastructures/capabilities must be
declared before /infrastructures/{infra_id} or the path parameter captures "capabilities"
as an infrastructure id and the dashboard silently gets a 404 (or someone else's infra).
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _service_urls(monkeypatch):
    for name in (
        "AUTH_SERVICE_URL", "USER_SERVICE_URL", "NOTIFICATION_SERVICE_URL",
        "INFRASTRUCTURE_SERVICE_URL", "APPLICATION_SERVICE_URL", "PAYMENT_SERVICE_URL",
    ):
        monkeypatch.setenv(name, os.environ.get(name, "http://svc:8000"))


def _infra_routes():
    from app.api.endpoints.infrastructure import router

    return [r.path for r in router.routes if "GET" in getattr(r, "methods", set())]


def test_capabilities_is_registered_before_the_infra_id_route():
    paths = _infra_routes()
    assert "/infrastructures/capabilities" in paths, paths
    assert "/infrastructures/{infra_id}" in paths, paths
    assert paths.index("/infrastructures/capabilities") < paths.index("/infrastructures/{infra_id}")


def test_capabilities_resolves_to_capabilities_not_an_infra_lookup():
    from app.api.router import api_router
    from fastapi import FastAPI
    from fastapi.routing import APIRoute

    app = FastAPI()
    app.include_router(api_router)

    matched = None
    for route in app.routes:
        if (
            isinstance(route, APIRoute)
            and "GET" in route.methods
            and route.path_regex.match("/infrastructures/capabilities")
        ):
            matched = route
            break

    assert matched is not None
    assert matched.name == "list_capabilities", f"resolved to {matched.name} instead"
