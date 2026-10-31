"""Migration 0045 re-runs the personal-org backfill for rows created after 0043 but before
create paths stamped the column. An Application takes its Infrastructure's org, whatever
that is. Idempotent, and never overwrites a stamped row."""
import importlib
import uuid

import pytest
from django.apps import apps
from shared.utils.org_id import personal_org_id

from api.tests.org_authz_support import make_db_user, make_stack

MIGRATION = importlib.import_module("api.migrations.0045_organization_id_rebackfill")

pytestmark = pytest.mark.django_db


def test_stamps_null_rows_and_apps_follow_their_infra(schema_db):
    owner = make_db_user()
    stale_infra, stale_app = make_stack(organization=None, app_organization=None, owner=owner)

    chosen_org = uuid.uuid4()
    stamped_infra, stamped_app = make_stack(organization=None, app_organization=None, owner=owner)
    type(stamped_infra).objects.filter(id=stamped_infra.id).update(organization_id=chosen_org)

    MIGRATION.backfill_organization_id(apps, None)
    MIGRATION.backfill_organization_id(apps, None)

    for row in (stale_infra, stale_app, stamped_infra, stamped_app):
        row.refresh_from_db()
    assert stale_infra.organization_id == personal_org_id(owner.id)
    assert stale_app.organization_id == stale_infra.organization_id
    assert stamped_infra.organization_id == chosen_org
    assert stamped_app.organization_id == chosen_org
