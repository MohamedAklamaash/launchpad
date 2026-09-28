"""App-delete cleanup: the pre-existing host_forward_rule_arn leak (enqueue_cleanup never
carried it, so ApplicationCleanupService's handling of it — Step 2.5 — was unreachable from
the real delete path) and the new custom-domain detach/notify step (F1b part 3b), which
runs unconditionally since CustomDomainRoute isn't tracked on Application itself."""
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from api.services.deployment_queue import DeploymentQueue


def test_enqueue_cleanup_carries_host_forward_rule_arn(monkeypatch):
    redis = MagicMock()
    monkeypatch.setattr(DeploymentQueue, "get_redis", staticmethod(lambda: redis))

    DeploymentQueue.enqueue_cleanup(
        app_id="a1", infrastructure_id="i1", target_group_arn="arn:tg",
        host_forward_rule_arn="arn:host-forward-rule",
    )

    job = json.loads(redis.rpush.call_args[0][1])
    assert job["host_forward_rule_arn"] == "arn:host-forward-rule"


def test_enqueue_cleanup_host_forward_rule_arn_defaults_to_none(monkeypatch):
    redis = MagicMock()
    monkeypatch.setattr(DeploymentQueue, "get_redis", staticmethod(lambda: redis))

    DeploymentQueue.enqueue_cleanup(app_id="a1", infrastructure_id="i1", target_group_arn="arn:tg")

    job = json.loads(redis.rpush.call_args[0][1])
    assert job["host_forward_rule_arn"] is None


@pytest.fixture
def owner(schema_db):
    from api.models.user import User

    return User.objects.create(id=uuid.uuid4(), email=f"o-{uuid.uuid4()}@e.io", user_name="owner")


@pytest.fixture
def infra(owner):
    from api.models.infrastructure import Infrastructure

    return Infrastructure.objects.create(
        user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=4, max_memory=8, code="123456789012",
    )


@pytest.fixture
def app(owner, infra):
    from api.models.application import Application

    return Application.objects.create(
        user=owner, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/acme/my-app", project_branch="main",
        project_commit_hash="abc123", target_group_arn="arn:tg", host_forward_rule_arn="arn:host-fwd",
    )


@pytest.fixture
def _stub_queue(monkeypatch):
    mock_enqueue = MagicMock()
    monkeypatch.setattr("api.services.application_service.DeploymentQueue.enqueue_cleanup", mock_enqueue)
    monkeypatch.setattr("api.services.application_service.ApplicationEventProducer.publish_application_deleted", MagicMock())
    monkeypatch.setattr("api.services.application_service.DeploymentLock", MagicMock())
    return mock_enqueue


@pytest.mark.django_db
def test_delete_application_enqueues_cleanup_with_host_forward_rule_arn(owner, app, _stub_queue):
    from api.services.application_service import ApplicationService

    with patch("api.services.custom_domain_routing.detach_custom_domains_for_application", return_value=[]):
        ApplicationService().delete_application(owner.id, app.id)

    _stub_queue.assert_called_once()
    assert _stub_queue.call_args.kwargs["host_forward_rule_arn"] == "arn:host-fwd"


@pytest.mark.django_db
def test_delete_application_detaches_custom_domains_and_notifies_when_present(owner, app, infra, _stub_queue):
    from api.services.application_service import ApplicationService

    with patch("api.services.custom_domain_routing.detach_custom_domains_for_application",
               return_value=["app.example.com"]) as detach, \
         patch("api.services.custom_domain_routing.notify_infrastructure_service_of_deleted_application") as notify:
        ApplicationService().delete_application(owner.id, app.id)

    detach.assert_called_once_with(app.id)
    notify.assert_called_once_with(str(infra.id), app.id)


@pytest.mark.django_db
def test_delete_application_notifies_even_with_no_local_custom_domain_routes(owner, app, infra, _stub_queue):
    """Security review RECOMMENDED: notify unconditionally, not only when a route
    existed here — a still-PENDING domain (claimed but never verified, so never attached
    in this service at all) also needs its certificate cleaned up on the
    infrastructure-service side, and this notification is the only signal that ever
    reaches it that the application is gone."""
    from api.services.application_service import ApplicationService

    with patch("api.services.custom_domain_routing.detach_custom_domains_for_application",
               return_value=[]) as detach, \
         patch("api.services.custom_domain_routing.notify_infrastructure_service_of_deleted_application") as notify:
        ApplicationService().delete_application(owner.id, app.id)

    detach.assert_called_once_with(app.id)
    notify.assert_called_once_with(str(infra.id), app.id)


@pytest.mark.django_db
def test_delete_application_survives_custom_domain_cleanup_failure(owner, app, _stub_queue):
    """App deletion must never fail because custom-domain teardown did."""
    from api.services.application_service import ApplicationService

    with patch("api.services.custom_domain_routing.detach_custom_domains_for_application",
               side_effect=RuntimeError("boom")):
        result = ApplicationService().delete_application(owner.id, app.id)

    assert result is not None
    from api.models.application import Application
    assert not Application.objects.filter(id=app.id).exists()
