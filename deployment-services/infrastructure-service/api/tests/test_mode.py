"""Tests for shared.mode — MODE=dev normalization and the dev-mode safety guard.

Pure-Python unit tests (no DB). Covers requirements 1 (normalize_mode/is_dev_mode)
and 2 (enforce_dev_mode_safety: dev always mocks; real creds are ignored, not fatal).
"""
import logging

import pytest
from shared import mode as mode_module
from shared.mode import (
    REAL_AWS_CREDENTIAL_ENV_VARS,
    enforce_dev_mode_safety,
    is_dev_mode,
    normalize_mode,
)

# --- Requirement 1: normalize_mode / is_dev_mode ---------------------------

@pytest.mark.parametrize(
    "raw",
    ["dev", "DEV", "  DEV  ", "Dev", "\tdev\n"],
)
def test_normalize_mode_dev_variants_normalize_to_dev(raw):
    assert normalize_mode(raw) == "dev"
    assert is_dev_mode(raw) is True


@pytest.mark.parametrize(
    "raw",
    ["prod", "PROD", "production", "", "   ", "foo", "develop", "devmode", None],
)
def test_normalize_mode_everything_else_is_prod(raw):
    # Fail-safe default: only exact normalized "dev" enables mocks.
    assert normalize_mode(raw) == "prod"
    assert is_dev_mode(raw) is False


def test_normalize_mode_substring_dev_is_not_dev():
    # "develop" contains "dev" but must NOT enable mocks.
    assert is_dev_mode("develop") is False


# --- Requirement 2: enforce_dev_mode_safety --------------------------------

@pytest.fixture
def clean_aws_env(monkeypatch):
    """Strip every AWS credential env var so detection starts from zero."""
    for name in REAL_AWS_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def logger():
    return logging.getLogger("test_mode")


@pytest.mark.parametrize(
    "var,value",
    [
        ("AWS_ACCESS_KEY_ID", "AKIAREALKEY1234567890"),
        ("AWS_PROFILE", "production"),
        ("AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/Real"),
    ],
)
def test_dev_mode_with_real_creds_mocks_without_exit(clean_aws_env, logger, caplog, var, value):
    clean_aws_env.setenv(var, value)
    with caplog.at_level(logging.WARNING, logger="test_mode"):
        enforce_dev_mode_safety("dev", "infrastructure-service", logger)
    assert "MOCKED" in caplog.text
    # The ignored real-cred var must still be surfaced so the mock path is never silent.
    assert var in caplog.text


def test_dev_mode_clean_env_does_not_exit(clean_aws_env, logger):
    # No real creds present -> dev boot is allowed.
    enforce_dev_mode_safety("dev", "infrastructure-service", logger)


def test_prod_mode_with_real_creds_does_not_exit(clean_aws_env, logger):
    # Prod is the normal real-AWS path; real creds are expected, never a hard exit.
    clean_aws_env.setenv("AWS_ACCESS_KEY_ID", "AKIAREALKEY1234567890")
    clean_aws_env.setenv("AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/Real")
    enforce_dev_mode_safety("prod", "infrastructure-service", logger)


def test_dev_mode_placeholder_creds_do_not_trip_guard(clean_aws_env, logger):
    # test_settings stubs AWS_ACCESS_KEY_ID="test"/SECRET="test"; placeholder-ish
    # values like "changeme"/"dev"/"mock" must NOT be treated as real creds.
    for placeholder in ("dev", "mock", "changeme", "placeholder", "test"):
        clean_aws_env.setenv("AWS_ACCESS_KEY_ID", placeholder)
        # Should not raise.
        enforce_dev_mode_safety("dev", "infrastructure-service", logger)


def test_detected_real_aws_credentials_ignores_empty_and_placeholder(clean_aws_env):
    clean_aws_env.setenv("AWS_ACCESS_KEY_ID", "")
    clean_aws_env.setenv("AWS_SECRET_ACCESS_KEY", "changeme")
    clean_aws_env.setenv("AWS_PROFILE", "real-profile")
    detected = mode_module.detected_real_aws_credentials()
    assert detected == ["AWS_PROFILE"]
