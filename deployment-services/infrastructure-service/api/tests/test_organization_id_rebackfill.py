"""Migration 0041 re-runs the personal-org backfill for rows created after 0039 but before
create paths stamped the column. Idempotent, and never overwrites a stamped row."""
import importlib
import uuid

import pytest
from django.apps import apps
from shared.utils.org_id import personal_org_id

from api.tests.org_authz_support import make_db_user, make_infra

MIGRATION = importlib.import_module("api.migrations.0041_organization_id_rebackfill")

pytestmark = pytest.mark.django_db


def test_stamps_null_rows_and_leaves_stamped_rows_alone():
    from api.models.database import Database
    from api.models.environment import Environment

    owner = make_db_user()
    stale = make_infra(owner=owner, organization=None)
    chosen_org = uuid.uuid4()
    stamped = make_infra(owner=owner, organization=None)
    type(stamped).objects.filter(id=stamped.id).update(organization_id=chosen_org)
    env = Environment.objects.get(infrastructure=stale)
    db = Database.objects.create(
        environment=env, name="db", engine="postgres", engine_version="16", instance_class="db.t3.micro",
    )

    MIGRATION.backfill_organization_id(apps, None)
    MIGRATION.backfill_organization_id(apps, None)

    stale.refresh_from_db()
    stamped.refresh_from_db()
    db.refresh_from_db()
    assert stale.organization_id == personal_org_id(owner.id)
    assert stamped.organization_id == chosen_org
    assert db.organization_id == personal_org_id(owner.id)
