import uuid
from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture
def seed(schema_db):
    from api.models.user import User
    from api.models.infrastructure import Infrastructure

    owner = User.objects.create(id=uuid.uuid4(), email=f"o-{uuid.uuid4()}@e.io", user_name="owner")
    infra = Infrastructure.objects.create(
        user=owner, name="i", cloud_provider="aws", max_cpu=1, max_memory=1, code="123456789012"
    )
    return owner, infra


def _call(infra_id, user_id):
    from api.views.infrastructure_validation import infrastructure_validation

    req = APIRequestFactory().get(f"/api/v1/infrastructures/{infra_id}/validation/")
    force_authenticate(req, user=SimpleNamespace(id=str(user_id), is_authenticated=True))
    return infrastructure_validation(req, infra_id=infra_id)


@pytest.mark.django_db
def test_stranger_cannot_read_validation_of_unowned_infra(seed):
    _, infra = seed
    resp = _call(infra.id, uuid.uuid4())
    assert resp.status_code == 404


@pytest.mark.django_db
def test_owner_can_read_validation(seed):
    owner, infra = seed
    resp = _call(infra.id, owner.id)
    assert resp.status_code == 200
    assert "app_count" in resp.data


@pytest.mark.django_db
def test_unknown_infra_id_is_404(seed):
    resp = _call(uuid.uuid4(), uuid.uuid4())
    assert resp.status_code == 404
