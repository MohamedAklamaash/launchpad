"""The dns_label backfill covers infrastructures that predate the column.

New rows get a label at create. Every already-onboarded row would keep NULL, and that gap
only shows up when HTTPS activation tries to build a hostname for them — a production
incident rather than a migration. These tests exercise the migration's own function
against the live models.
"""

import importlib
import uuid

import pytest
from django.apps import apps as django_apps_registry

# The module name is not a valid Python identifier, so it cannot be imported normally.
backfill_module = importlib.import_module("api.migrations.0023_backfill_dns_label")


@pytest.fixture
def make_infra(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(**kwargs):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t",
        )
        return Infrastructure.objects.create(
            user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", metadata={}, **kwargs,
        )
    return _make


def test_backfill_gives_every_unlabelled_infra_a_label(make_infra):
    from api.models.infrastructure import Infrastructure

    infras = [make_infra() for _ in range(3)]
    Infrastructure.objects.filter(id__in=[i.id for i in infras]).update(dns_label=None)

    backfill_module.backfill(django_apps_registry, None)

    labels = []
    for infra in infras:
        infra.refresh_from_db()
        assert infra.dns_label is not None
        labels.append(infra.dns_label)
    assert len(set(labels)) == len(labels), "labels must be unique across infrastructures"


def test_backfill_reserves_each_label_so_it_is_never_reissued(make_infra):
    from api.models.infrastructure import Infrastructure
    from api.models.reserved_dns_label import ReservedDnsLabel

    infra = make_infra()
    Infrastructure.objects.filter(id=infra.id).update(dns_label=None)

    backfill_module.backfill(django_apps_registry, None)

    infra.refresh_from_db()
    assert ReservedDnsLabel.objects.filter(label=infra.dns_label).exists()


def test_backfill_leaves_already_labelled_rows_untouched(make_infra):
    infra = make_infra()
    infra.mint_dns_label()
    original = infra.dns_label

    backfill_module.backfill(django_apps_registry, None)

    infra.refresh_from_db()
    assert infra.dns_label == original


def test_backfill_is_idempotent(make_infra):
    from api.models.infrastructure import Infrastructure
    from api.models.reserved_dns_label import ReservedDnsLabel

    infra = make_infra()
    Infrastructure.objects.filter(id=infra.id).update(dns_label=None)

    backfill_module.backfill(django_apps_registry, None)
    infra.refresh_from_db()
    first = infra.dns_label
    reservations = ReservedDnsLabel.objects.filter(infrastructure_id=infra.id).count()

    backfill_module.backfill(django_apps_registry, None)
    infra.refresh_from_db()
    assert infra.dns_label == first
    assert ReservedDnsLabel.objects.filter(infrastructure_id=infra.id).count() == reservations


def test_reverse_keeps_the_tombstones(make_infra):
    """Reversing must not free labels for reuse — handing a retired label to a different
    tenant is the exact collision this column exists to prevent."""
    from api.models.infrastructure import Infrastructure
    from api.models.reserved_dns_label import ReservedDnsLabel

    infra = make_infra()
    Infrastructure.objects.filter(id=infra.id).update(dns_label=None)
    backfill_module.backfill(django_apps_registry, None)
    before = set(ReservedDnsLabel.objects.values_list('label', flat=True))

    backfill_module.unbackfill(django_apps_registry, None)

    assert set(ReservedDnsLabel.objects.values_list('label', flat=True)) == before
