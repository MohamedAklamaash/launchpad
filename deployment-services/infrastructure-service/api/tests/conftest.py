"""Fixtures for the F9 ticket 7 authorization tests: no Redis, no broker, no AWS."""
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
def no_budget(monkeypatch):
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)
    monkeypatch.setattr("api.views.exit_export.customer_call_budget", lambda *a, **k: None)


@pytest.fixture
def orgs_on(settings, fake_role_cache, no_budget):
    settings.ORGS_ENABLED = True


@pytest.fixture
def orgs_off(settings, fake_role_cache, no_budget):
    settings.ORGS_ENABLED = False


@pytest.fixture
def factory():
    return APIRequestFactory()
