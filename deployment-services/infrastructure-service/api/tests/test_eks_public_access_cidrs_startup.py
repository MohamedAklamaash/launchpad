"""core/settings.py refuses to import (so the service refuses to start) outside MODE=dev
when EKS_ENABLED=true and EKS_PUBLIC_ACCESS_CIDRS is empty, malformed, or too broad.

Run as a subprocess importing core.settings (not test_settings): the pytest process
itself is already configured against test_settings, and Django settings are a
process-wide singleton that cannot be re-pointed once loaded.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent

BASE_ENV = {
    "PATH": os.environ.get("PATH", ""),
    "HOME": os.environ.get("HOME", "/tmp"),
    "DJANGO_SETTINGS_MODULE": "core.settings",
    "DJANGO_SECRET": "x" * 60,
    "JWT_SECRET": "x" * 40,
    "DJANGO_PORT": "8002",
    "INTERNAL_API_TOKEN": "x" * 40,
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "DATABASE_USER_NAME": "test",
    "DATABASE_PASSWORD": "test",
    "DATABASE_HOST": "localhost",
    "DATABASE_PORT": "5432",
    "DATABASE_NAME": "test",
    "INFRASTRUCTURE_DB_URL": "postgres://test:test@localhost/test",
    "LAUNCHPAD_PLATFORM_PRINCIPAL_ARN": "arn:aws:iam::123456789012:user/x",
    "PLATFORM_BASE_DOMAIN": "launchpad.test",
}


def _boot(extra_env: dict) -> subprocess.CompletedProcess:
    env = {**BASE_ENV, **extra_env}
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=SERVICE_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("bad_cidrs", ["", "0.0.0.0/0", "0.0.0.0/1,128.0.0.0/1", "not-a-cidr"])
def test_refuses_to_start_with_eks_enabled_and_bad_cidrs(bad_cidrs):
    result = _boot({"MODE": "prod", "EKS_ENABLED": "true", "EKS_PUBLIC_ACCESS_CIDRS": bad_cidrs})
    assert result.returncode != 0
    assert "EKS_PUBLIC_ACCESS_CIDRS" in result.stderr


def test_boots_with_eks_enabled_and_a_valid_cidr():
    """Positive control: proves the check above fires on the bad config, not on
    anything else in the settings module."""
    result = _boot({"MODE": "prod", "EKS_ENABLED": "true", "EKS_PUBLIC_ACCESS_CIDRS": "203.0.113.0/24"})
    assert result.returncode == 0, result.stderr


def test_boots_with_eks_disabled_despite_bad_cidrs():
    result = _boot({"MODE": "prod", "EKS_ENABLED": "false", "EKS_PUBLIC_ACCESS_CIDRS": ""})
    assert result.returncode == 0, result.stderr


def test_boots_in_dev_mode_despite_bad_cidrs():
    result = _boot({"MODE": "dev", "EKS_ENABLED": "true", "EKS_PUBLIC_ACCESS_CIDRS": ""})
    assert result.returncode == 0, result.stderr
