"""Migration 0043 backfill: every pre-existing Infrastructure/Application row gets a
nullable organization_id stamped with the owner's personal-org id, with no call to
auth-service (plan/F9-teams-and-orgs.md). Application is backfilled from its
parent Infrastructure's owner, never from its own `user` (the app's creator, who can be
an invited admin distinct from the infra owner)."""
import importlib
import uuid

import pytest
from django.apps import apps
from shared.utils.org_id import personal_org_id

MIGRATION = importlib.import_module("api.migrations.0043_organization_id_backfill")


def _backfill():
    MIGRATION.backfill_organization_id(apps, None)


@pytest.fixture
def fixtures(schema_db):
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make_user():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t",
        )

    def _make_infra(*, user=None, **overrides):
        user = user or _make_user()
        return Infrastructure.objects.create(
            user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, **overrides,
        )

    def _make_app(*, infra, user=None, **overrides):
        user = user or infra.user
        return Application.objects.create(
            user=user, infrastructure=infra, name=f"app-{uuid.uuid4()}",
            project_remote_url="https://github.com/x/y", project_branch="main",
            project_commit_hash="", envs={}, attached_database_ids=[], **overrides,
        )

    return type("Fixtures", (), {"make_user": staticmethod(_make_user),
                                   "make_infra": staticmethod(_make_infra),
                                   "make_app": staticmethod(_make_app)})


def test_backfill_stamps_infrastructure_with_owner_personal_org_id(fixtures):
    infra = fixtures.make_infra()
    assert infra.organization_id is None

    _backfill()

    infra.refresh_from_db()
    assert infra.organization_id == personal_org_id(infra.user_id)


def test_backfill_stamps_application_from_parent_infrastructure_owner_not_creator(fixtures):
    """The app's creator (an invited admin) is a different user than the infra's owner
    -- the app must still land in the owner's org, not the creator's."""
    owner = fixtures.make_user()
    creator = fixtures.make_user()
    infra = fixtures.make_infra(user=owner)
    app = fixtures.make_app(infra=infra, user=creator)
    assert app.organization_id is None
    assert app.user_id != infra.user_id

    _backfill()

    app.refresh_from_db()
    assert app.organization_id == personal_org_id(owner.id)
    assert app.organization_id != personal_org_id(creator.id)


def test_backfill_is_idempotent(fixtures):
    infra = fixtures.make_infra()
    app = fixtures.make_app(infra=infra)

    _backfill()
    infra.refresh_from_db()
    app.refresh_from_db()
    infra_org_id = infra.organization_id
    app_org_id = app.organization_id

    _backfill()

    infra.refresh_from_db()
    app.refresh_from_db()
    assert infra.organization_id == infra_org_id
    assert app.organization_id == app_org_id


def test_backfill_never_overwrites_an_already_set_organization_id(fixtures):
    pinned = uuid.uuid4()
    infra = fixtures.make_infra(organization_id=pinned)

    _backfill()

    infra.refresh_from_db()
    assert infra.organization_id == pinned


def test_migration_uses_the_shared_helper():
    import shared.utils.org_id as org_id_module

    assert MIGRATION.personal_org_id is org_id_module.personal_org_id


def test_personal_org_id_matches_the_cross_service_vector():
    """Same fixed vector asserted in shared/tests/test_org_id.py and in
    infrastructure-service's and payment-service's own suites -- run here, in this
    service's own interpreter/venv, so a broken symlink or a vendored copy of
    shared/utils/org_id.py would fail this service's own test run, not just shared's."""
    legacy_user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    expected = uuid.UUID("622fe5e9-2e23-53f5-a533-9fb6a4a8bd14")
    assert personal_org_id(legacy_user_id) == expected
