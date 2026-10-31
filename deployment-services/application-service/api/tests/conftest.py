"""Fixtures for the F9 ticket 7 authorization tests: no Redis, no broker, no AWS."""
from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_role_cache(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("shared.services.org_role_cache._redis", lambda: fake)
    return fake


@pytest.fixture
def no_side_effects(monkeypatch):
    """Budget, concurrency slots, queue and lock: nothing here may touch Redis."""
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)
    for module in ("runtime_logs", "build_logs", "app_metrics"):
        monkeypatch.setattr(f"api.views.{module}.customer_call_budget", lambda *a, **k: None)
    for module in ("runtime_logs", "build_logs"):
        monkeypatch.setattr(f"api.views.{module}.acquire_concurrency_slot", lambda *a, **k: True)
        monkeypatch.setattr(f"api.views.{module}.release_concurrency_slot", lambda *a, **k: None)
    queue = MagicMock()
    for target in ("api.services.application_service.DeploymentQueue", "api.views.application.DeploymentQueue",
                   "api.services.rollback_service.DeploymentQueue"):
        monkeypatch.setattr(target, queue)
    lock = MagicMock()
    lock.return_value.acquire.return_value = True
    lock.return_value.is_locked.return_value = False
    for target in ("api.services.application_service.DeploymentLock", "api.services.rollback_service.DeploymentLock"):
        monkeypatch.setattr(target, lock)
    return queue


@pytest.fixture
def orgs_on(settings, fake_role_cache, no_side_effects):
    settings.ORGS_ENABLED = True


@pytest.fixture
def orgs_off(settings, fake_role_cache, no_side_effects):
    settings.ORGS_ENABLED = False


@pytest.fixture
def factory():
    return APIRequestFactory()
