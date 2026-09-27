"""RECOMMENDED: core.wsgi/core.asgi refuse to start under LAUNCHPAD_PROCESS_ROLE=dns_writer
— that role must run only via manage.py run_dns_writer / sweep_platform_dns, never as a
web server process.
"""
import importlib
import sys

import pytest


def _reimport_raises(module_name):
    sys.modules.pop(module_name, None)
    with pytest.raises(RuntimeError, match="dns_writer"):
        importlib.import_module(module_name)
    sys.modules.pop(module_name, None)


def test_wsgi_refuses_to_start_as_dns_writer(monkeypatch):
    monkeypatch.setenv("LAUNCHPAD_PROCESS_ROLE", "dns_writer")
    _reimport_raises("core.wsgi")


def test_asgi_refuses_to_start_as_dns_writer(monkeypatch):
    monkeypatch.setenv("LAUNCHPAD_PROCESS_ROLE", "dns_writer")
    _reimport_raises("core.asgi")
