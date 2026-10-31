"""F9 ticket 7: billing is owner-only under ORGS_ENABLED (plan matrix row 15). Driven at the
HTTP view layer; Stripe is stubbed at PaymentService, the Redis role cache with a dict.
Flag off, the views behave as before: no ownership check on the infrastructure."""
import logging
import sys
import uuid
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.authz.org_matrix import MATRIX, Capability
from shared.enums.org_role import OrgRole
from shared.utils.jwt import JWTUser

try:
    import stripe  # noqa: F401
except ImportError:
    # The SDK is only needed so api.services.payment_service can be imported; every Stripe
    # call is patched out below. CI installs the real package and never takes this branch.
    _stub = ModuleType("stripe")
    _stub.checkout = SimpleNamespace(Session=SimpleNamespace(create=None, retrieve=None))
    _stub.PaymentIntent = SimpleNamespace(create=None)
    _stub.error = SimpleNamespace(StripeError=Exception, SignatureVerificationError=Exception)
    _stub.Webhook = SimpleNamespace(construct_event=None)
    sys.modules["stripe"] = _stub

pytestmark = pytest.mark.django_db

ROLES = [r.value for r in OrgRole]


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def fake_role_cache(monkeypatch):
    monkeypatch.setattr("shared.services.org_role_cache._redis", lambda: _FakeRedis())


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def payer(db):
    from api.models.user import User

    return User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")


@pytest.fixture
def org(db):
    from api.models.organization import Organization

    return Organization.objects.create(id=uuid.uuid4(), owner_account_id=uuid.uuid4(), version=1)


def _infra(organization):
    from api.models.infrastructure import Infrastructure

    return Infrastructure.objects.create(
        id=uuid.uuid4(), user_id=uuid.uuid4(), name="i", organization_id=organization.id if organization else None,
    )


def _member(org, role, *, removed=False):
    from django.utils import timezone

    from api.models.membership import Membership

    account_id = uuid.uuid4()
    Membership.objects.create(organization=org, account_id=account_id, role=role, version=1,
                              removed_at=timezone.now() if removed else None)
    return account_id


def _token(payer, org, account_id):
    return JWTUser(sub=str(payer.id), org_id=str(org.id), account_id=str(account_id) if account_id else None)


def _checkout(factory, user, infra_id):
    from api.views.payment import create_checkout_session

    request = factory.post("/x/", {"amount": 2000, "infrastructure_id": str(infra_id)}, format="json")
    force_authenticate(request, user=user)
    return create_checkout_session(request)


def _direct(factory, user, infra_id):
    from api.views.payment import process_payment

    request = factory.post(
        "/x/", {"amount": 2000, "payment_method_id": "stub-payment-method", "infrastructure_id": str(infra_id)}, format="json",
    )
    force_authenticate(request, user=user)
    return process_payment(request)


SITES = [_checkout, _direct]
STRIPE_OK = SimpleNamespace(url="https://stripe.example/checkout", id="stub-session")


@pytest.fixture
def stripe_ok():
    import api.views.payment  # noqa: F401

    with patch("api.views.payment.payment_service.create_checkout_session", return_value=STRIPE_OK), \
         patch("api.views.payment.payment_service.process_direct_payment", return_value={"success": True}):
        yield


@pytest.fixture
def orgs_on(settings):
    settings.ORGS_ENABLED = True


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
@pytest.mark.parametrize("role", ROLES)
def test_billing_matrix(orgs_on, stripe_ok, factory, payer, org, site, role):
    infra = _infra(org)
    resp = site(factory, _token(payer, org, _member(org, role)), infra.id)
    assert resp.status_code == (200 if role in MATRIX[Capability.BILLING] else 403)


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_infrastructure_in_another_org_is_404_even_for_an_owner_there(orgs_on, stripe_ok, factory, payer, org, site):
    from api.models.membership import Membership
    from api.models.organization import Organization

    other = Organization.objects.create(id=uuid.uuid4(), owner_account_id=uuid.uuid4(), version=1)
    account = _member(org, "owner")
    Membership.objects.create(organization=other, account_id=account, role="owner", version=1)
    infra = _infra(other)
    assert site(factory, _token(payer, org, account), infra.id).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_removed_owner_is_404(orgs_on, stripe_ok, factory, payer, org, site):
    infra = _infra(org)
    assert site(factory, _token(payer, org, _member(org, "owner", removed=True)), infra.id).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_null_org_infrastructure_is_inaccessible_and_logged(orgs_on, stripe_ok, factory, payer, org, site, caplog):
    infra = _infra(None)
    with caplog.at_level(logging.WARNING):
        resp = site(factory, _token(payer, org, _member(org, "owner")), infra.id)
    assert resp.status_code == 404
    assert "no organization_id" in caplog.text


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_token_without_account_id_cannot_bill(orgs_on, stripe_ok, factory, payer, org, site):
    infra = _infra(org)
    assert site(factory, _token(payer, org, None), infra.id).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_unknown_or_malformed_infrastructure_is_404(orgs_on, stripe_ok, factory, payer, org, site):
    owner = _token(payer, org, _member(org, "owner"))
    assert site(factory, owner, uuid.uuid4()).status_code == 404
    assert site(factory, owner, "not-a-uuid").status_code == 404


def test_pending_billing_row_is_stamped_with_the_infrastructure_org(orgs_on, factory, payer, org):
    from api.models.billing import Billing

    infra = _infra(org)
    import api.services.payment_service  # noqa: F401

    fake_session = SimpleNamespace(url="https://stripe.example/checkout", id="stub-session")
    with patch("api.services.payment_service.stripe_cb.execute", return_value=fake_session):
        resp = _checkout(factory, _token(payer, org, _member(org, "owner")), infra.id)
    assert resp.status_code == 200
    assert Billing.objects.get(user_id=payer.id).organization_id == org.id


def test_flag_off_keeps_the_pre_f9_behaviour_no_ownership_check(settings, stripe_ok, factory, payer, org):
    settings.ORGS_ENABLED = False
    stranger_infra = _infra(org)
    plain = JWTUser(sub=str(payer.id))
    assert _checkout(factory, plain, stranger_infra.id).status_code == 200
    assert _direct(factory, plain, stranger_infra.id).status_code == 200


def test_infra_mirror_stores_organization_id_write_once(org):
    from api.models.infrastructure import Infrastructure
    from api.repositories.infrastructure import InfrastructureRepository

    infra_id = uuid.uuid4()
    base = {"id": infra_id, "user_id": uuid.uuid4(), "name": "m", "status": "active"}
    repo = InfrastructureRepository()
    repo.upsert_infrastructure({**base, "organization_id": str(org.id)})
    repo.upsert_infrastructure({**base, "organization_id": str(uuid.uuid4())})
    repo.upsert_infrastructure({**base, "organization_id": None})
    assert Infrastructure.objects.get(id=infra_id).organization_id == org.id


def test_infra_mirror_fills_a_null_organization_id(org):
    from api.models.infrastructure import Infrastructure
    from api.repositories.infrastructure import InfrastructureRepository

    infra = _infra(None)
    InfrastructureRepository().upsert_infrastructure(
        {"id": infra.id, "user_id": infra.user_id, "name": "m", "status": "active", "organization_id": str(org.id)},
    )
    assert Infrastructure.objects.get(id=infra.id).organization_id == org.id
