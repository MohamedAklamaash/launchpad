"""Migration 0008: a Billing row takes the organization_id of the infrastructure named in its
metadata, falls back to the payer's personal org, and never overwrites a stamped row."""
import importlib
import uuid

import pytest
from django.apps import apps
from shared.utils.org_id import personal_org_id

from api.models.billing import Billing
from api.models.infrastructure import Infrastructure

MIGRATION = importlib.import_module("api.migrations.0008_organization_id_rebackfill")

pytestmark = pytest.mark.django_db


def _billing(user_id, infra_id=None, organization_id=None):
    return Billing.objects.create(
        user_id=user_id, amount=1, month=1, year=2026, organization_id=organization_id,
        metadata={"infrastructure_id": str(infra_id)} if infra_id else {},
    )


def test_billing_follows_its_infrastructure_then_the_payer():
    payer = uuid.uuid4()
    org = uuid.uuid4()
    infra = Infrastructure.objects.create(id=uuid.uuid4(), user_id=payer, name="i", organization_id=org)
    chosen = uuid.uuid4()
    via_infra = _billing(payer, infra.id)
    fallback = _billing(payer)
    stamped = _billing(payer, infra.id, organization_id=chosen)

    MIGRATION.backfill_organization_id(apps, None)
    MIGRATION.backfill_organization_id(apps, None)

    for row in (via_infra, fallback, stamped):
        row.refresh_from_db()
    assert via_infra.organization_id == org
    assert fallback.organization_id == personal_org_id(payer)
    assert stamped.organization_id == chosen
