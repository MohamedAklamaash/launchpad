"""Migration 0006 backfill: every pre-existing Infrastructure/Billing row gets a
nullable organization_id stamped with the owner's personal-org id, computed straight
off user_id, with no call to auth-service (plan/F9-teams-and-orgs.md)."""
import importlib
import uuid
from decimal import Decimal

import pytest
from django.apps import apps
from shared.utils.org_id import personal_org_id

MIGRATION = importlib.import_module("api.migrations.0006_organization_id_backfill")


def _backfill():
    MIGRATION.backfill_organization_id(apps, None)


@pytest.fixture
def make_infra(db):
    from api.models.infrastructure import Infrastructure

    def _make(*, user_id=..., **overrides):
        user_id = uuid.uuid4() if user_id is ... else user_id
        return Infrastructure.objects.create(id=uuid.uuid4(), user_id=user_id, **overrides)

    return _make


@pytest.fixture
def make_billing(db):
    from api.models.billing import Billing

    def _make(*, user_id=None, **overrides):
        user_id = user_id or uuid.uuid4()
        return Billing.objects.create(
            id=uuid.uuid4(), user_id=user_id, amount=Decimal("10.00"),
            month=1, year=2026, **overrides,
        )

    return _make


def test_backfill_stamps_infrastructure_with_owner_personal_org_id(make_infra):
    infra = make_infra()
    assert infra.organization_id is None

    _backfill()

    infra.refresh_from_db()
    assert infra.organization_id == personal_org_id(infra.user_id)


def test_backfill_stamps_billing_with_owner_personal_org_id(make_billing):
    billing = make_billing()
    assert billing.organization_id is None

    _backfill()

    billing.refresh_from_db()
    assert billing.organization_id == personal_org_id(billing.user_id)


def test_backfill_leaves_infrastructure_with_null_user_id_alone(make_infra):
    """Infrastructure.user_id is nullable here ('resilience') -- a row with no owner
    has nothing to compute an org id from, and must not blow up the migration."""
    infra = make_infra(user_id=None)

    _backfill()

    infra.refresh_from_db()
    assert infra.organization_id is None


def test_backfill_is_idempotent(make_infra, make_billing):
    infra = make_infra()
    billing = make_billing()

    _backfill()
    infra.refresh_from_db()
    billing.refresh_from_db()
    infra_org_id = infra.organization_id
    billing_org_id = billing.organization_id

    _backfill()

    infra.refresh_from_db()
    billing.refresh_from_db()
    assert infra.organization_id == infra_org_id
    assert billing.organization_id == billing_org_id


def test_backfill_never_overwrites_an_already_set_organization_id(make_billing):
    pinned = uuid.uuid4()
    billing = make_billing(organization_id=pinned)

    _backfill()

    billing.refresh_from_db()
    assert billing.organization_id == pinned


@pytest.mark.django_db
def test_backfill_does_not_move_billing_updated_at():
    """Billing is a money table -- the backfill must use queryset.update(), not
    instance.save(), so it never touches auto_now fields on a financial record."""
    from api.models.billing import Billing

    user_id = uuid.uuid4()
    billing = Billing.objects.create(
        id=uuid.uuid4(), user_id=user_id, amount=Decimal("10.00"), month=1, year=2026,
    )
    stamped_at = billing.updated_at

    _backfill()

    billing.refresh_from_db()
    assert billing.updated_at == stamped_at
    assert billing.organization_id == personal_org_id(user_id)


def test_migration_uses_the_shared_helper():
    import shared.utils.org_id as org_id_module

    assert MIGRATION.personal_org_id is org_id_module.personal_org_id


def test_personal_org_id_matches_the_cross_service_vector():
    """Same fixed vector asserted in shared/tests/test_org_id.py and in
    infrastructure-service's and application-service's own suites -- run here, in this
    service's own venv, so a broken symlink or a vendored copy of
    shared/utils/org_id.py would fail this service's own test run, not just shared's."""
    legacy_user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    expected = uuid.UUID("622fe5e9-2e23-53f5-a533-9fb6a4a8bd14")
    assert personal_org_id(legacy_user_id) == expected
