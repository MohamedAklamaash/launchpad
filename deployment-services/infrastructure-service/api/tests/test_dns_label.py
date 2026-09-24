"""Infrastructure.dns_label: collision-safe minting, never derived from id, and never
reissued once the owning infrastructure is hard-deleted."""
import uuid

import pytest


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role="super_admin",
        )

    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make():
        return Infrastructure.objects.create(
            user=make_user(), name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012",
        )

    return _make


def test_mint_dns_label_persists_reserved_row(make_infra):
    from api.models.reserved_dns_label import ReservedDnsLabel

    infra = make_infra()
    label = infra.mint_dns_label()

    assert infra.dns_label == label
    reservation = ReservedDnsLabel.objects.get(label=label)
    assert reservation.infrastructure_id == infra.id


def test_mint_dns_label_is_idempotent(make_infra):
    from api.models.reserved_dns_label import ReservedDnsLabel

    infra = make_infra()
    first = infra.mint_dns_label()
    second = infra.mint_dns_label()

    assert first == second
    assert ReservedDnsLabel.objects.filter(infrastructure_id=infra.id).count() == 1


def test_mint_dns_label_retries_on_collision(make_infra, monkeypatch):
    from api.models import infrastructure as infrastructure_module
    from api.models.reserved_dns_label import ReservedDnsLabel

    taken = make_infra()
    taken_label = taken.mint_dns_label()

    values = iter([taken_label, "freshvalue000000"])
    monkeypatch.setattr(infrastructure_module.secrets, "token_hex", lambda n: next(values))

    infra = make_infra()
    label = infra.mint_dns_label()

    assert label == "freshvalue000000"
    assert ReservedDnsLabel.objects.filter(infrastructure_id=infra.id).count() == 1


def test_mint_dns_label_raises_after_exhausting_retries(make_infra, monkeypatch):
    from api.models import infrastructure as infrastructure_module

    taken = make_infra()
    taken_label = taken.mint_dns_label()

    monkeypatch.setattr(infrastructure_module.secrets, "token_hex", lambda n: taken_label)

    infra = make_infra()
    with pytest.raises(infrastructure_module.DnsLabelMintExhausted):
        infra.mint_dns_label()
    assert infra.dns_label is None


def test_mint_dns_label_never_derived_from_id(make_infra):
    infra = make_infra()
    label = infra.mint_dns_label()

    assert label not in str(infra.id).replace("-", "")
    assert str(infra.id)[:8] not in label


def test_dns_label_not_reused_after_infrastructure_destroyed(make_infra, monkeypatch):
    from api.models import infrastructure as infrastructure_module
    from api.models.reserved_dns_label import ReservedDnsLabel

    infra = make_infra()
    label = infra.mint_dns_label()
    infra_id = infra.id

    infra.delete()
    assert not infrastructure_module.Infrastructure.objects.filter(id=infra_id).exists()
    reservation = ReservedDnsLabel.objects.get(label=label)
    assert reservation.infrastructure_id == infra_id

    values = iter([label, "freshvalue111111"])
    monkeypatch.setattr(infrastructure_module.secrets, "token_hex", lambda n: next(values))

    new_infra = make_infra()
    new_label = new_infra.mint_dns_label()

    assert new_label == "freshvalue111111"
    assert new_label != label
