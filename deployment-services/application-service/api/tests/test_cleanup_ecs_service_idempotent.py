"""_delete_ecs_service must finish a delete an earlier attempt started (real AWS: attempt 1
timed out while the service was DRAINING, attempt 2 hit ServiceNotActiveException)."""
from unittest.mock import MagicMock

import pytest

from api.services import application_cleanup_service as mod
from api.services.application_cleanup_service import ApplicationCleanupService


class _NotActive(Exception):
    pass


class _NotFound(Exception):
    pass


def _client(statuses, update_side_effect=None):
    ecs = MagicMock()
    ecs.exceptions.ServiceNotActiveException = _NotActive
    ecs.exceptions.ServiceNotFoundException = _NotFound
    it = iter(statuses)
    ecs.describe_services.side_effect = lambda **kw: {"services": [{"status": next(it)}]}
    if update_side_effect:
        ecs.update_service.side_effect = update_side_effect
    session = MagicMock()
    session.client.return_value = ecs
    return session, ecs


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_retry_on_a_draining_service_only_waits(monkeypatch):
    session, ecs = _client(["DRAINING", "DRAINING", "INACTIVE"])
    ApplicationCleanupService()._delete_ecs_service(session, "cluster", "arn:svc/app-service")
    ecs.update_service.assert_not_called()
    ecs.delete_service.assert_not_called()


def test_already_inactive_service_is_a_no_op():
    session, ecs = _client(["INACTIVE"])
    ApplicationCleanupService()._delete_ecs_service(session, "cluster", "arn:svc/app-service")
    ecs.update_service.assert_not_called()


def test_service_going_inactive_mid_delete_is_not_an_error():
    session, _ecs = _client(["ACTIVE", "INACTIVE"], update_side_effect=_NotActive("not active"))
    ApplicationCleanupService()._delete_ecs_service(session, "cluster", "arn:svc/app-service")


def test_wait_outlasts_the_default_alb_deregistration_delay():
    assert mod.ECS_SERVICE_INACTIVE_TIMEOUT_SECONDS > 300
