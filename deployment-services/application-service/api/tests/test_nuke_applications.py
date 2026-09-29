"""Nuke infrastructure's apps step: ApplicationService.nuke_applications_for_infrastructure
force-deletes every Application on an infra synchronously (AWS cleanup, then the row),
unlike delete_application's async-enqueue path — and the internal view that fronts it."""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory


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
def make_app(owner, infra):
    from api.models.application import Application

    def _make(**overrides):
        defaults = {
            "user": owner, "infrastructure": infra, "name": f"app-{uuid.uuid4().hex[:6]}",
            "project_remote_url": "https://github.com/acme/app", "project_branch": "main",
            "project_commit_hash": "abc123", "target_group_arn": "arn:tg", "service_arn": "arn:svc",
        }
        defaults.update(overrides)
        return Application.objects.create(**defaults)
    return _make


@pytest.mark.django_db
def test_nuke_deletes_every_application_and_returns_log_groups(infra, make_app):
    from api.models.application import Application
    from api.services.application_service import ApplicationService

    app1 = make_app(name="app-one")
    app2 = make_app(name="app-two")

    with patch("api.services.application_service.ApplicationCleanupService.cleanup_application") as cleanup, \
         patch("api.services.custom_domain_routing.detach_custom_domains_for_application", return_value=[]), \
         patch("api.services.custom_domain_routing.notify_infrastructure_service_of_deleted_application"), \
         patch("api.services.application_service.ApplicationEventProducer.publish_application_deleted"):
        result = ApplicationService().nuke_applications_for_infrastructure(str(infra.id))

    assert result["deleted_count"] == 2
    assert result["errors"] == []
    assert set(result["log_groups"]) == {
        f"/ecs/{app1.name}-task", f"/ecs/{app2.name}-task",
    }
    assert cleanup.call_count == 2
    assert not Application.objects.filter(infrastructure_id=infra.id).exists()


@pytest.mark.django_db
def test_nuke_records_errors_and_keeps_processing_other_apps(infra, make_app):
    from api.models.application import Application
    from api.services.application_service import ApplicationService

    good = make_app(name="good-app")
    bad = make_app(name="bad-app")

    def _cleanup(self, app):
        if app.id == bad.id:
            raise RuntimeError("ECS won't drain")

    with patch("api.services.application_service.ApplicationCleanupService.cleanup_application", _cleanup), \
         patch("api.services.custom_domain_routing.detach_custom_domains_for_application", return_value=[]), \
         patch("api.services.custom_domain_routing.notify_infrastructure_service_of_deleted_application"), \
         patch("api.services.application_service.ApplicationEventProducer.publish_application_deleted"):
        result = ApplicationService().nuke_applications_for_infrastructure(str(infra.id))

    assert result["deleted_count"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["application_id"] == str(bad.id)
    assert not Application.objects.filter(id=good.id).exists()
    assert Application.objects.filter(id=bad.id).exists()  # left in place for a nuke retry


@pytest.mark.django_db
def test_nuke_skips_ecs_log_group_capture_for_eks_apps(infra, make_app):
    """EKS apps are cleaned up via delete_runtime_resources (runtime_refs), not the
    ECS log-group-keeping path — capturing an /ecs/ log group name for them would be
    meaningless and the leftovers step would try to delete a name that never existed."""
    from api.services.application_service import ApplicationService

    make_app(name="eks-app", runtime_refs={"runtime": "eks", "namespace": "app-eks-app"})

    with patch("api.services.application_service.ApplicationCleanupService.cleanup_application"), \
         patch("api.services.custom_domain_routing.detach_custom_domains_for_application", return_value=[]), \
         patch("api.services.custom_domain_routing.notify_infrastructure_service_of_deleted_application"), \
         patch("api.services.application_service.ApplicationEventProducer.publish_application_deleted"):
        result = ApplicationService().nuke_applications_for_infrastructure(str(infra.id))

    assert result["deleted_count"] == 1
    assert result["log_groups"] == []


@pytest.mark.django_db
def test_nuke_is_idempotent_with_no_applications(infra):
    from api.services.application_service import ApplicationService

    result = ApplicationService().nuke_applications_for_infrastructure(str(infra.id))
    assert result == {"deleted_count": 0, "errors": [], "log_groups": []}


# ── internal view ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_nuke_internal_view_returns_summary(infra):
    from api.views.nuke_internal import nuke_applications

    factory = APIRequestFactory()
    request = factory.post(
        "/api/v1/internal/applications/nuke/", {"infrastructure_id": str(infra.id)}, format="json",
    )
    with patch(
        "api.services.application_service.ApplicationService.nuke_applications_for_infrastructure",
        return_value={"deleted_count": 0, "errors": [], "log_groups": []},
    ) as nuke:
        response = nuke_applications(request)

    assert response.status_code == 200
    assert response.data == {"deleted_count": 0, "errors": [], "log_groups": []}
    nuke.assert_called_once_with(str(infra.id))


def test_nuke_internal_view_rejects_invalid_body():
    from api.views.nuke_internal import nuke_applications

    factory = APIRequestFactory()
    request = factory.post("/api/v1/internal/applications/nuke/", {}, format="json")
    response = nuke_applications(request)
    assert response.status_code == 400


def test_nuke_internal_path_is_jwt_exempt_and_not_internal_auth_exempt():
    """The trust boundary: no user JWT is ever sent (JWTAuthMiddleware must not demand
    one), but X-INTERNAL-TOKEN is still required (InternalAuthMiddleware must NOT
    exempt it) — see nuke_internal.py's module docstring."""
    from shared.middleware.authentication import EXEMPT_EXACT_PATHS

    assert "/api/v1/internal/applications/nuke/" in EXEMPT_EXACT_PATHS

    from django.conf import settings
    assert "/api/v1/internal/applications/nuke/" not in getattr(settings, "INTERNAL_AUTH_EXEMPT_PATHS", [])
    assert not any(
        "/api/v1/internal/applications/nuke/".startswith(prefix)
        for prefix in getattr(settings, "INTERNAL_AUTH_EXEMPT_PREFIXES", [])
    )


# ── task definition delete-after-deregister ─────────────────────────────────────

@pytest.mark.django_db
def test_deregister_task_definition_also_deletes_the_revision():
    from api.services.application_cleanup_service import ApplicationCleanupService

    session = MagicMock()
    ecs_client = session.client.return_value
    ApplicationCleanupService()._deregister_task_definition(session, "arn:aws:ecs:...:task-definition/x:3")

    ecs_client.deregister_task_definition.assert_called_once_with(taskDefinition="arn:aws:ecs:...:task-definition/x:3")
    ecs_client.delete_task_definitions.assert_called_once_with(taskDefinitions=["arn:aws:ecs:...:task-definition/x:3"])


@pytest.mark.django_db
def test_deregister_task_definition_delete_failure_is_non_fatal():
    from api.services.application_cleanup_service import ApplicationCleanupService

    session = MagicMock()
    ecs_client = session.client.return_value
    ecs_client.delete_task_definitions.side_effect = RuntimeError("not supported in this region")

    # Must not raise — a best-effort improvement, never the thing that fails a cleanup.
    ApplicationCleanupService()._deregister_task_definition(session, "arn:x")
