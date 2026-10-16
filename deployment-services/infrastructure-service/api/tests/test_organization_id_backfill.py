"""Migration 0039 backfill: every pre-existing Infrastructure/Database row gets a
nullable organization_id stamped with the owner's personal-org id, with no call to
auth-service (plan/F9-teams-and-orgs.md)."""
import importlib
import uuid

import pytest
from django.apps import apps
from shared.utils.org_id import personal_org_id

MIGRATION = importlib.import_module("api.migrations.0039_organization_id_backfill")


def _backfill():
    MIGRATION.backfill_organization_id(apps, None)


@pytest.fixture
def make_infra(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(**overrides):
        user = overrides.pop("user", None) or User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin"
        )
        return Infrastructure.objects.create(
            user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", **overrides,
        )

    return _make


@pytest.fixture
def make_database(make_infra):
    from api.models.database import Database
    from api.models.environment import Environment

    def _make(*, infra=None):
        infra = infra or make_infra()
        env = Environment.objects.create(infrastructure=infra)
        return Database.objects.create(
            environment=env, name="db", engine="postgres", engine_version="16",
            instance_class="db.t3.micro",
        )

    return _make


def test_backfill_stamps_infrastructure_with_owner_personal_org_id(make_infra):
    infra = make_infra()
    assert infra.organization_id is None

    _backfill()

    infra.refresh_from_db()
    assert infra.organization_id == personal_org_id(infra.user_id)


def test_backfill_stamps_database_from_parent_infrastructure_owner(make_database, make_infra):
    infra = make_infra()
    database = make_database(infra=infra)
    assert database.organization_id is None

    _backfill()

    database.refresh_from_db()
    assert database.organization_id == personal_org_id(infra.user_id)


def test_backfill_is_idempotent(make_infra, make_database):
    infra = make_infra()
    database = make_database(infra=infra)

    _backfill()
    infra.refresh_from_db()
    database.refresh_from_db()
    infra_org_id = infra.organization_id
    database_org_id = database.organization_id

    _backfill()

    infra.refresh_from_db()
    database.refresh_from_db()
    assert infra.organization_id == infra_org_id
    assert database.organization_id == database_org_id


def test_backfill_never_overwrites_an_already_set_organization_id(make_infra):
    pinned = uuid.uuid4()
    infra = make_infra(organization_id=pinned)

    _backfill()

    infra.refresh_from_db()
    assert infra.organization_id == pinned


def test_backfill_handles_two_infras_sharing_an_owner(make_infra):
    from api.models.user import User

    user = User.objects.create(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin"
    )
    infra_a = make_infra(user=user)
    infra_b = make_infra(user=user)

    _backfill()

    infra_a.refresh_from_db()
    infra_b.refresh_from_db()
    expected = personal_org_id(user.id)
    assert infra_a.organization_id == expected
    assert infra_b.organization_id == expected


def test_migration_uses_the_shared_helper():
    import shared.utils.org_id as org_id_module

    assert MIGRATION.personal_org_id is org_id_module.personal_org_id


def test_personal_org_id_matches_the_cross_service_vector():
    """Same fixed vector asserted in shared/tests/test_org_id.py and in
    application-service's and payment-service's own suites -- run here, in this
    service's own interpreter/venv, so a broken symlink or a vendored copy of
    shared/utils/org_id.py would fail this service's own test run, not just shared's."""
    legacy_user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    expected = uuid.UUID("622fe5e9-2e23-53f5-a533-9fb6a4a8bd14")
    assert personal_org_id(legacy_user_id) == expected
