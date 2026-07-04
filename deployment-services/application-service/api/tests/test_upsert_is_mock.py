"""Requirement 7: is_mock is indelible — upsert_infrastructure never flips True -> False.

DB-backed via the schema_db fixture (tables built from model state, since a full
migrate is blocked by the pre-existing Postgres-only migration 0005 on SQLite).
"""
import uuid

import pytest


@pytest.fixture
def make_user(schema_db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(),
            email=f"u-{uuid.uuid4()}@example.com",
            user_name="tester",
        )

    return _make


@pytest.fixture
def repo():
    from api.repositories.infrastructure import InfrastructureRepository

    return InfrastructureRepository()


def _base_payload(user_id, infra_id, **overrides):
    payload = {
        "id": str(infra_id),
        "user_id": str(user_id),
        "name": "infra-x",
        "cloud_provider": "aws",
        "max_cpu": 1.0,
        "max_memory": 2.0,
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_mock_true_persists_then_cannot_be_flipped_false(repo, make_user):
    user = make_user()
    infra_id = uuid.uuid4()

    # First upsert: created as mock.
    infra, created = repo.upsert_infrastructure(_base_payload(user.id, infra_id, is_mock=True))
    assert created is True
    assert infra.is_mock is True

    # Second upsert tries to flip it back to real — must stay mock.
    infra2, created2 = repo.upsert_infrastructure(
        _base_payload(user.id, infra_id, is_mock=False, name="renamed")
    )
    assert created2 is False
    assert infra2.is_mock is True
    infra2.refresh_from_db()
    assert infra2.is_mock is True


@pytest.mark.django_db
def test_mock_omitted_on_subsequent_upsert_stays_mock(repo, make_user):
    user = make_user()
    infra_id = uuid.uuid4()
    repo.upsert_infrastructure(_base_payload(user.id, infra_id, is_mock=True))

    # Event without is_mock at all (e.g. environment.updated) must not clear it.
    infra2, _ = repo.upsert_infrastructure(_base_payload(user.id, infra_id))
    assert infra2.is_mock is True


@pytest.mark.django_db
def test_real_infra_stays_real(repo, make_user):
    user = make_user()
    infra_id = uuid.uuid4()
    infra, _ = repo.upsert_infrastructure(_base_payload(user.id, infra_id, is_mock=False))
    assert infra.is_mock is False
    infra2, _ = repo.upsert_infrastructure(_base_payload(user.id, infra_id))
    assert infra2.is_mock is False


@pytest.mark.django_db
def test_upsert_replaces_stale_same_user_name_row(repo, make_user):
    # Regression: infra-service enforces unique (user, name), so a same-(user, name)
    # read-model row with a different id is stale (its upstream delete never arrived).
    # The upsert must drop it and materialize the authoritative event instead of dying
    # on the unique constraint and discarding the event (which left app creation broken
    # with "Infrastructure not found").
    from api.models.infrastructure import Infrastructure

    user = make_user()
    stale_id, fresh_id = uuid.uuid4(), uuid.uuid4()

    repo.upsert_infrastructure(_base_payload(user.id, stale_id, name="dup"))
    infra, created = repo.upsert_infrastructure(_base_payload(user.id, fresh_id, name="dup"))

    assert created is True
    assert str(infra.id) == str(fresh_id)
    assert not Infrastructure.objects.filter(id=stale_id).exists()
    assert Infrastructure.objects.filter(id=fresh_id).exists()


@pytest.mark.django_db
def test_delete_infrastructure_removes_row_idempotently(repo, make_user):
    # Regression: infrastructure.deleted consumer must drop the read-model row and be a
    # no-op if it's already gone (redelivery / double delete).
    from api.models.infrastructure import Infrastructure

    user = make_user()
    infra_id = uuid.uuid4()
    repo.upsert_infrastructure(_base_payload(user.id, infra_id))
    assert Infrastructure.objects.filter(id=infra_id).exists()

    assert repo.delete_infrastructure(infra_id) is True
    assert not Infrastructure.objects.filter(id=infra_id).exists()
    assert repo.delete_infrastructure(infra_id) is False
