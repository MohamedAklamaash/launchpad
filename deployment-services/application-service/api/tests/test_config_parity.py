"""Requirement 8: both services' ApplicationConfig expose `mode` and agree on the
dev/prod mapping (both derive it from shared.mode.normalize_mode).

Loaded by file path because the two ApplicationConfig modules live under the same
`api.common.envs.application` dotted name in different services; importlib avoids
the namespace clash. Both call from_env() at import, so env is stubbed first.
"""
import importlib.util
import os

import pytest

_HERE = os.path.dirname(__file__)
# api/tests/ -> application-service root is two levels up.
_APP_SVC = os.path.abspath(os.path.join(_HERE, "..", ".."))
INFRA_APP_CFG = os.path.abspath(
    os.path.join(
        _APP_SVC, "..", "infrastructure-service", "api", "common", "envs", "application.py"
    )
)
APP_APP_CFG = os.path.abspath(os.path.join(_APP_SVC, "api", "common", "envs", "application.py"))


def _load(monkeypatch, path, name, mode="prod"):
    # Provide every env var both from_env() implementations dereference,
    # isolated per-test via monkeypatch so nothing leaks into the suite.
    for k, v in {
        "MODE": mode,
        "DJANGO_SECRET": "x" * 60,
        "JWT_SECRET": "x" * 40,
        "DJANGO_PORT": "8000",
        "INTERNAL_API_TOKEN": "x" * 40,
        "RABBITMQ_URL": "amqp://guest:guest@localhost:5672/",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6379",
        "REDIS_PASSWORD": "",
        "REDIS_DB": "0",
        "DEPLOYMENT_MAX_INFRA_WORKERS": "5",
    }.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_both_configs_expose_mode_field(monkeypatch):
    infra = _load(monkeypatch, INFRA_APP_CFG, "_infra_app_cfg")
    app = _load(monkeypatch, APP_APP_CFG, "_app_app_cfg")
    assert hasattr(infra.ApplicationConfig, "__dataclass_fields__")
    assert "mode" in infra.ApplicationConfig.__dataclass_fields__
    assert "mode" in app.ApplicationConfig.__dataclass_fields__


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("dev", "dev"),
        ("DEV", "dev"),
        ("  DEV  ", "dev"),
        ("prod", "prod"),
        ("", "prod"),
        ("foo", "prod"),
    ],
)
def test_both_configs_agree_on_dev_prod_mapping(monkeypatch, raw, expected):
    infra = _load(monkeypatch, INFRA_APP_CFG, f"_infra_cfg_{abs(hash(raw))}", mode=raw)
    app = _load(monkeypatch, APP_APP_CFG, f"_app_cfg_{abs(hash(raw))}", mode=raw)
    infra_cfg = infra.ApplicationConfig.from_env()
    app_cfg = app.ApplicationConfig.from_env()
    assert infra_cfg.mode == expected
    assert app_cfg.mode == expected
    assert infra_cfg.mode == app_cfg.mode
