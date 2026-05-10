import importlib

import pytest
from pydantic import ValidationError


@pytest.fixture
def settings_cls(monkeypatch):
    # Provide a baseline of valid env so Settings instantiation only depends on
    # the URL we override per-test.
    baseline = {
        "AUTH_SERVICE_URL": "http://auth-service:5001",
        "USER_SERVICE_URL": "http://user-service:5002",
        "NOTIFICATION_SERVICE_URL": "http://notification-service:5003",
        "INFRASTRUCTURE_SERVICE_URL": "http://infra-service:8002",
        "APPLICATION_SERVICE_URL": "http://app-service:8001",
        "PAYMENT_SERVICE_URL": "http://payment-service:8003",
    }
    for k, v in baseline.items():
        monkeypatch.setenv(k, v)

    import app.core.config as config_mod
    importlib.reload(config_mod)
    return config_mod.Settings


def test_normalize_returns_bare_host_port_unchanged(settings_cls):
    s = settings_cls(AUTH_SERVICE_URL="http://auth-service:5001")
    assert s.AUTH_SERVICE_URL == "http://auth-service:5001"


def test_normalize_strips_trailing_slash(settings_cls):
    s = settings_cls(AUTH_SERVICE_URL="http://auth-service:5001/")
    assert s.AUTH_SERVICE_URL == "http://auth-service:5001"


def test_normalize_strips_trailing_api_v1(settings_cls, caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="app.core.config")
    s = settings_cls(AUTH_SERVICE_URL="http://auth-service:5001/api/v1")
    assert s.AUTH_SERVICE_URL == "http://auth-service:5001"
    assert any("trailing /api/v1" in rec.message for rec in caplog.records)


def test_normalize_strips_trailing_api_v1_with_slash(settings_cls, caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="app.core.config")
    s = settings_cls(AUTH_SERVICE_URL="http://auth-service:5001/api/v1/")
    assert s.AUTH_SERVICE_URL == "http://auth-service:5001"
    assert any("trailing /api/v1" in rec.message for rec in caplog.records)


def test_normalize_rejects_arbitrary_path(settings_cls):
    with pytest.raises(ValidationError) as exc_info:
        settings_cls(AUTH_SERVICE_URL="http://auth-service:5001/foo/bar")
    msg = str(exc_info.value)
    assert "AUTH_SERVICE_URL" in msg
    assert "/foo/bar" in msg


def test_normalize_applied_to_payment_service_url(settings_cls):
    s = settings_cls(PAYMENT_SERVICE_URL="http://payment-service:8003/api/v1")
    assert s.PAYMENT_SERVICE_URL == "http://payment-service:8003"


def test_normalize_rejects_path_on_infrastructure_service_url(settings_cls):
    with pytest.raises(ValidationError) as exc_info:
        settings_cls(INFRASTRUCTURE_SERVICE_URL="http://infra:8002/v2/whatever")
    assert "INFRASTRUCTURE_SERVICE_URL" in str(exc_info.value)
