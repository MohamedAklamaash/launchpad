"""F6: an infrastructure that completed the exit flow (Infrastructure.exited_at set) must
refuse the mutating actions infrastructure-service itself owns — reprovision (re-runs
Terraform) and config update. Deploy/app-create in application-service are not gated here;
see plan/F6-exit-export.md for that follow-up."""
import uuid
from unittest.mock import MagicMock

import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )
    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(*, exited=False):
        owner = make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", is_cloud_authenticated=True,
        )
        if exited:
            infra.exited_at = timezone.now()
            infra.save(update_fields=["exited_at"])
        return owner, infra
    return _make


@pytest.fixture(autouse=True)
def _stub_infra_queue(monkeypatch):
    monkeypatch.setattr("api.services.infra_queue.InfraQueue.enqueue_provision", MagicMock())


@pytest.mark.django_db
def test_reprovision_refused_once_exited(make_infra):
    from api.views.infrastructure import infrastructure_reprovision

    owner, infra = make_infra(exited=True)
    request = APIRequestFactory().post(f"/api/v1/infrastructures/{infra.id}/reprovision/")
    force_authenticate(request, user=owner)

    resp = infrastructure_reprovision(request, infra_id=str(infra.id))
    assert resp.status_code == 409


@pytest.mark.django_db
def test_reprovision_allowed_when_not_exited(make_infra):
    from api.views.infrastructure import infrastructure_reprovision

    owner, infra = make_infra(exited=False)
    request = APIRequestFactory().post(f"/api/v1/infrastructures/{infra.id}/reprovision/")
    force_authenticate(request, user=owner)

    resp = infrastructure_reprovision(request, infra_id=str(infra.id))
    assert resp.status_code != 409


@pytest.mark.django_db
def test_update_refused_once_exited(make_infra):
    from api.views.infrastructure import infrastructure_update

    owner, infra = make_infra(exited=True)
    request = APIRequestFactory().patch(
        f"/api/v1/infrastructures/{infra.id}/update/", {"name": "new-name"}, format="json",
    )
    force_authenticate(request, user=owner)

    resp = infrastructure_update(request, infra_id=str(infra.id))
    assert resp.status_code == 400
    assert "exited" in resp.data["error"]


@pytest.mark.django_db
def test_update_allowed_when_not_exited(make_infra):
    from api.views.infrastructure import infrastructure_update

    owner, infra = make_infra(exited=False)
    request = APIRequestFactory().patch(
        f"/api/v1/infrastructures/{infra.id}/update/", {"name": "new-name"}, format="json",
    )
    force_authenticate(request, user=owner)

    resp = infrastructure_update(request, infra_id=str(infra.id))
    assert resp.status_code == 200
