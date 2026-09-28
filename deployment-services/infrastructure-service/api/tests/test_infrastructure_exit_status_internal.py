"""Internal exit-status lookup — H2 (hardening) RECOMMENDED 2. Pure machine-to-machine,
no user JWT context (see api/views/infrastructure_internal.py's docstring)."""
import uuid

import pytest
from api.views.infrastructure_internal import infrastructure_exit_status
from rest_framework.test import APIRequestFactory

factory = APIRequestFactory()


@pytest.fixture
def make_infra(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(**overrides):
        user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
        defaults = {
            "id": uuid.uuid4(), "user": user, "name": f"infra-{uuid.uuid4()}", "cloud_provider": "aws",
            "max_cpu": 1024, "max_memory": 512,
        }
        defaults.update(overrides)
        return Infrastructure.objects.create(**defaults)
    return _make


def test_rejects_missing_infrastructure_id():
    request = factory.get("/api/v1/internal/infrastructures/exit-status/")

    response = infrastructure_exit_status(request)

    assert response.status_code == 400


def test_rejects_malformed_infrastructure_id():
    request = factory.get("/api/v1/internal/infrastructures/exit-status/", {"infrastructure_id": "not-a-uuid"})

    response = infrastructure_exit_status(request)

    assert response.status_code == 400


@pytest.mark.django_db
def test_returns_404_for_unknown_infrastructure():
    request = factory.get("/api/v1/internal/infrastructures/exit-status/", {"infrastructure_id": str(uuid.uuid4())})

    response = infrastructure_exit_status(request)

    assert response.status_code == 404


@pytest.mark.django_db
def test_returns_null_exited_at_for_an_active_infrastructure(make_infra):
    infra = make_infra()
    request = factory.get("/api/v1/internal/infrastructures/exit-status/", {"infrastructure_id": str(infra.id)})

    response = infrastructure_exit_status(request)

    assert response.status_code == 200
    assert response.data == {"infrastructure_id": str(infra.id), "exited_at": None}


@pytest.mark.django_db
def test_returns_the_real_exited_at_for_an_exited_infrastructure(make_infra):
    from django.utils import timezone

    infra = make_infra(exited_at=timezone.now())
    request = factory.get("/api/v1/internal/infrastructures/exit-status/", {"infrastructure_id": str(infra.id)})

    response = infrastructure_exit_status(request)

    assert response.status_code == 200
    assert response.data["infrastructure_id"] == str(infra.id)
    assert response.data["exited_at"] is not None
