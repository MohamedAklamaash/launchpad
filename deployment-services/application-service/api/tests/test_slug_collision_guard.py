"""A second user must not be able to take over an existing app's Kubernetes objects by
picking a name that differs only in case or punctuation. Every k8s object name derives
from app_slug(), so 'MyApp' and 'myapp' collapse onto one namespace."""
import uuid

import pytest

from api.common.naming import app_slug
from api.services.application_service import ApplicationService
from shared.enums.orchestrator import ComputeType


@pytest.fixture
def owner(schema_db):
    from api.models.user import User
    return User.objects.create(id=uuid.uuid4(), email=f"a-{uuid.uuid4()}@e.io", user_name="a")


@pytest.fixture
def infra(owner):
    from api.models.infrastructure import Infrastructure
    return Infrastructure.objects.create(
        user=owner, name="shared", cloud_provider="aws", code="123456789012",
        max_cpu=16.0, max_memory=32.0, compute_type=ComputeType.EKS,
    )


def _payload(infra, name):
    return {
        "infrastructure_id": str(infra.id), "name": name,
        "alloted_cpu": 1.0, "alloted_memory": 1.0,
        "project_remote_url": "https://github.com/o/r",
    }


@pytest.mark.parametrize("first,second", [("myapp", "MyApp"), ("my-app", "My App")])
def test_names_colliding_on_slug_are_refused(monkeypatch, infra, owner, first, second):
    from api.models.application import Application

    assert app_slug(first) == app_slug(second)
    monkeypatch.setattr(
        "api.services.infrastructure_permissions.InfrastructurePermissions.can_create_application",
        staticmethod(lambda *_a, **_k: True),
    )
    monkeypatch.setattr("api.services.deployment_queue.DeploymentQueue.enqueue_deployment", lambda *a, **k: None)
    monkeypatch.setattr(
        "api.messaging.producer.producer.ApplicationEventProducer.publish_application_created",
        staticmethod(lambda *a, **k: None),
    )

    service = ApplicationService()
    service.create_application(owner, _payload(infra, first))

    with pytest.raises(ValueError, match="same identifier"):
        service.create_application(owner, _payload(infra, second))

    assert Application.objects.filter(infrastructure_id=infra.id).count() == 1


def test_name_undeployable_on_k8s_is_refused_before_build(monkeypatch, infra, owner):
    monkeypatch.setattr(
        "api.services.infrastructure_permissions.InfrastructurePermissions.can_create_application",
        staticmethod(lambda *_a, **_k: True),
    )
    with pytest.raises(ValueError, match="not deployable on Kubernetes"):
        ApplicationService().create_application(owner, _payload(infra, "my.app"))
