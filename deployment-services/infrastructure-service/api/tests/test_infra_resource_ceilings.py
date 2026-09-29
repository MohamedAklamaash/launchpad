"""Infrastructure.max_cpu/max_memory (vCPU / GB ceilings across all apps) were
previously unvalidated server-side: a caller could set zero, negative, or an
unbounded ceiling. create/update now reject those with 400."""
import uuid

import pytest
from api.views.infrastructure import infrastructure_list_create, infrastructure_update
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture
def owner(db):
    from api.models.user import User

    return User.objects.create(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
    )


def _create(owner, **overrides):
    body = {
        "name": "prod-infra",
        "cloud_provider": "AWS",
        "code": "123456789012",
        "max_cpu": 4,
        "max_memory": 8,
        **overrides,
    }
    request = APIRequestFactory().post("/api/v1/infrastructures/", body, format="json")
    force_authenticate(request, user=owner)
    return infrastructure_list_create(request)


def _update(owner, infra_id, **overrides):
    request = APIRequestFactory().patch(f"/api/v1/infrastructures/{infra_id}/update/", overrides, format="json")
    force_authenticate(request, user=owner)
    return infrastructure_update(request, infra_id)


@pytest.mark.django_db
def test_create_accepts_a_valid_ceiling(owner):
    response = _create(owner, max_cpu=4, max_memory=8)
    assert response.status_code == 201


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", [
    ("max_cpu", 0),
    ("max_cpu", -1),
    ("max_cpu", 257),
    ("max_cpu", "not-a-number"),
    ("max_cpu", None),
    ("max_memory", 0),
    ("max_memory", -1),
    ("max_memory", 2049),
    ("max_memory", "not-a-number"),
    ("max_memory", None),
])
def test_create_rejects_a_bad_ceiling(owner, field, value):
    response = _create(owner, **{field: value})
    assert response.status_code == 400
    assert field in response.data["error"]


@pytest.mark.django_db
def test_update_accepts_a_valid_ceiling(owner):
    created = _create(owner).data
    response = _update(owner, created["id"], max_cpu=8, max_memory=16)
    assert response.status_code == 200
    assert response.data["max_cpu"] == 8
    assert response.data["max_memory"] == 16


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", [
    ("max_cpu", 0),
    ("max_cpu", -1),
    ("max_cpu", 257),
    ("max_memory", 0),
    ("max_memory", 2049),
])
def test_update_rejects_a_bad_ceiling(owner, field, value):
    created = _create(owner).data
    response = _update(owner, created["id"], **{field: value})
    assert response.status_code == 400
    assert field in response.data["error"]


@pytest.mark.django_db
def test_update_of_one_field_does_not_revalidate_a_stale_stored_ceiling(owner):
    """A row written before server-side bounds existed (or under a since-lowered
    settings.INFRA_MAX_CPU_VCPU) has a max_cpu that would now fail validation. Updating
    only max_memory must not re-check the untouched max_cpu against today's cap."""
    from api.models.infrastructure import Infrastructure

    created = _create(owner).data
    Infrastructure.objects.filter(id=created["id"]).update(max_cpu=4096)

    response = _update(owner, created["id"], max_memory=16)
    assert response.status_code == 200
    assert response.data["max_cpu"] == 4096
    assert response.data["max_memory"] == 16


@pytest.mark.django_db
def test_bounds_are_settings_configurable(owner, settings):
    settings.INFRA_MAX_CPU_VCPU = 8
    response = _create(owner, max_cpu=16, max_memory=8)
    assert response.status_code == 400
    assert "max_cpu" in response.data["error"]
