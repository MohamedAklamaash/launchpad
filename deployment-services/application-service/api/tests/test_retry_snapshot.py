"""Retry and delete must snapshot runtime_refs the way they snapshot ARNs, or the k8s
objects of the previous attempt are orphaned the moment the column is nulled."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REFS = {
    "runtime": "eks", "namespace": "app-myapp", "configmap": "myapp-nginx",
    "deployment": "myapp", "service": "myapp", "ingress": "myapp",
}


@pytest.fixture
def application(schema_db):
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name="infra-x", cloud_provider="aws", compute_type="eks",
        max_cpu=4.0, max_memory=8.0, code="000000000000",
    )
    return Application.objects.create(
        user=user, infrastructure=infra, name="myapp",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="", status="FAILED", runtime_refs=REFS,
    )


@pytest.fixture
def enqueued(monkeypatch):
    from api.services.deployment_queue import DeploymentQueue

    cleanup = MagicMock()
    monkeypatch.setattr(DeploymentQueue, "enqueue_cleanup", staticmethod(cleanup))
    monkeypatch.setattr(DeploymentQueue, "enqueue_deployment", staticmethod(MagicMock()))
    return cleanup


@pytest.mark.django_db
def test_retry_snapshots_and_nulls_runtime_refs(application, enqueued, monkeypatch):
    from api.views.application import ApplicationRetryDeployView

    monkeypatch.setattr(
        "api.services.infrastructure_permissions.InfrastructurePermissions.can_update_application",
        staticmethod(lambda *_a: True),
    )
    request = SimpleNamespace(user=SimpleNamespace(id=application.user_id))

    response = ApplicationRetryDeployView().post(request, pk=str(application.id))

    assert response.status_code == 202
    application.refresh_from_db()
    assert application.runtime_refs is None
    assert enqueued.call_args.kwargs["runtime"] == "eks"
    assert enqueued.call_args.kwargs["refs"] == REFS


@pytest.mark.django_db
def test_delete_snapshots_runtime_refs_into_the_cleanup_job(application, enqueued, monkeypatch):
    from api.services.application_service import ApplicationService

    monkeypatch.setattr(
        "api.services.infrastructure_permissions.InfrastructurePermissions.can_delete_application",
        staticmethod(lambda *_a: True),
    )
    monkeypatch.setattr("api.services.application_service.DeploymentLock", MagicMock())
    monkeypatch.setattr(
        "api.services.application_service.ApplicationEventProducer.publish_application_deleted",
        staticmethod(MagicMock()),
    )

    ApplicationService().delete_application(str(application.user_id), str(application.id))

    assert enqueued.call_args.kwargs["runtime"] == "eks"
    assert enqueued.call_args.kwargs["refs"] == REFS


@pytest.mark.django_db
def test_ecs_application_still_enqueues_the_legacy_shape(application, enqueued, monkeypatch):
    from api.services.application_service import ApplicationService

    application.runtime_refs = None
    application.target_group_arn = "arn:aws:elasticloadbalancing:::targetgroup/t/1"
    application.save()
    monkeypatch.setattr(
        "api.services.infrastructure_permissions.InfrastructurePermissions.can_delete_application",
        staticmethod(lambda *_a: True),
    )
    monkeypatch.setattr("api.services.application_service.DeploymentLock", MagicMock())
    monkeypatch.setattr(
        "api.services.application_service.ApplicationEventProducer.publish_application_deleted",
        staticmethod(MagicMock()),
    )

    ApplicationService().delete_application(str(application.user_id), str(application.id))

    assert enqueued.call_args.kwargs["runtime"] is None
    assert enqueued.call_args.kwargs["target_group_arn"].endswith("targetgroup/t/1")
