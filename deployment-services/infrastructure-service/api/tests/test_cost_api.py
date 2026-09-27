"""Per-app cost attribution API (F4): owner-only authz, mock-vs-real branching, Cost
Explorer actuals for ECS, labelled estimates for EKS, caching, policy-refresh mapping,
and the per-user rate budget."""
import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture(autouse=True)
def _stub_rate_budget(monkeypatch):
    """These views are budget-limited (F0 pattern). Most tests aren't about the budget
    itself, and no Redis is available in the test environment."""
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: None)


@pytest.fixture
def factory():
    return APIRequestFactory()


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

    def _make(*, owner=None, compute_type="ecs_fargate", is_mock=False, code="123456789012"):
        owner = owner or make_user()
        return Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code=code, metadata={},
            compute_type=compute_type, is_mock=is_mock,
        )
    return _make


@pytest.fixture
def make_app(db):
    from api.models.application import Application

    def _make(infra, name="my-app", alloted_cpu=0.5, alloted_memory=1.0):
        return Application.objects.create(
            id=str(uuid.uuid4()), infrastructure_id=str(infra.id), name=name,
            user_id=str(infra.user_id), alloted_cpu=alloted_cpu, alloted_memory=alloted_memory,
        )
    return _make


def _get_costs(factory, user, infra_id, months=None):
    from api.views.costs import infrastructure_costs

    query = f"?months={months}" if months is not None else ""
    request = factory.get(f"/api/v1/infrastructures/{infra_id}/costs/{query}")
    force_authenticate(request, user=user)
    return infrastructure_costs(request, infra_id=infra_id)


# ── authz ────────────────────────────────────────────────────────────────────────

def test_owner_can_view_costs(factory, make_infra, make_app):
    owner_infra = make_infra(is_mock=True)
    make_app(owner_infra)
    resp = _get_costs(factory, owner_infra.user, str(owner_infra.id))
    assert resp.status_code == 200


def test_invited_member_gets_403(factory, make_infra, make_user):
    infra = make_infra(is_mock=True)
    other = make_user()
    infra.invited_users.add(other)
    resp = _get_costs(factory, other, str(infra.id))
    assert resp.status_code == 403


def test_stranger_gets_404(factory, make_infra, make_user):
    infra = make_infra(is_mock=True)
    stranger = make_user()
    resp = _get_costs(factory, stranger, str(infra.id))
    assert resp.status_code == 404


def test_malformed_infra_id_returns_404(factory, make_user):
    resp = _get_costs(factory, make_user(), "not-a-uuid")
    assert resp.status_code == 404


# ── mock infrastructures ───────────────────────────────────────────────────────────

def test_mock_infra_returns_deterministic_mock_data_without_aws_calls(factory, make_infra, make_app, monkeypatch):
    infra = make_infra(is_mock=True)
    make_app(infra, name="my-app")

    def _boom(*a, **k):
        raise AssertionError("must not touch AWS for a mock infrastructure")

    monkeypatch.setattr("api.services.cost_service.authenticate_infrastructure", _boom)

    resp = _get_costs(factory, infra.user, str(infra.id))
    assert resp.status_code == 200
    assert resp.data["is_mock"] is True
    assert all(app["source"] == "mock" for app in resp.data["apps"])
    assert resp.data["shared"]["source"] == "mock"

    # Deterministic: same infra/window produces the same figures without caching.
    resp2 = _get_costs(factory, infra.user, str(infra.id))
    assert resp2.data["apps"] == resp.data["apps"]


# ── EKS estimate ─────────────────────────────────────────────────────────────────

def test_eks_infra_returns_labelled_estimates_for_every_figure(factory, make_infra, make_app, settings):
    infra = make_infra(compute_type="eks", is_mock=False)
    make_app(infra, name="api", alloted_cpu=1.0, alloted_memory=2.0)

    resp = _get_costs(factory, infra.user, str(infra.id), months=1)

    assert resp.status_code == 200
    assert resp.data["compute_type"] == "eks"
    assert all(app["source"] == "estimate" for app in resp.data["apps"])
    assert resp.data["shared"]["source"] == "estimate"
    assert resp.data["tag_activation"]["activated"] is None


def test_eks_estimate_arithmetic(factory, make_infra, make_app, settings):
    settings.COST_ESTIMATE_VCPU_HOUR_USD = 0.04
    settings.COST_ESTIMATE_GB_HOUR_USD = 0.01
    settings.COST_ESTIMATE_EKS_CONTROL_PLANE_HOUR_USD = 0.10

    infra = make_infra(compute_type="eks", is_mock=False)
    make_app(infra, name="api", alloted_cpu=1.0, alloted_memory=2.0)

    resp = _get_costs(factory, infra.user, str(infra.id), months=1)

    window_end = timezone.now().date()
    window_start = window_end - timedelta(days=30)
    hours = (window_end - window_start).days * 24
    expected_app = round(1.0 * 0.04 * hours + 2.0 * 0.01 * hours, 2)
    expected_shared = round(0.10 * hours, 2)

    assert resp.data["apps"][0]["amount_usd"] == expected_app
    assert resp.data["shared"]["amount_usd"] == expected_shared


# ── ECS actuals via Cost Explorer ─────────────────────────────────────────────────

def _ce_response(app_amount="12.34", shared_amount="5.00"):
    return {
        "ResultsByTime": [{
            "Groups": [
                {"Keys": ["launchpad:app$my-app"], "Metrics": {"UnblendedCost": {"Amount": app_amount, "Unit": "USD"}}},
                {"Keys": ["launchpad:app$"], "Metrics": {"UnblendedCost": {"Amount": shared_amount, "Unit": "USD"}}},
            ],
        }],
    }


@pytest.fixture
def fake_ce(monkeypatch):
    ce = MagicMock()
    ce.get_cost_and_usage.return_value = _ce_response()
    ce.update_cost_allocation_tags_status.return_value = {}
    monkeypatch.setattr("api.services.cost_service._ce_client", lambda infra: ce)
    return ce


def test_ecs_actuals_are_grouped_by_app_tag_and_marked_actual(factory, make_infra, make_app, fake_ce):
    infra = make_infra(compute_type="ecs_fargate", is_mock=False)
    make_app(infra, name="my-app")

    resp = _get_costs(factory, infra.user, str(infra.id))

    assert resp.status_code == 200
    assert resp.data["apps"] == [{"app": "my-app", "amount_usd": 12.34, "source": "actual"}]
    assert resp.data["shared"]["amount_usd"] == 5.0
    assert resp.data["shared"]["source"] == "actual"
    assert resp.data["tag_activation"]["activated"] is True


def test_cache_hit_never_calls_cost_explorer_again(factory, make_infra, make_app, fake_ce):
    infra = make_infra(compute_type="ecs_fargate", is_mock=False)
    make_app(infra, name="my-app")

    first = _get_costs(factory, infra.user, str(infra.id))
    assert first.status_code == 200
    assert fake_ce.get_cost_and_usage.call_count == 1

    second = _get_costs(factory, infra.user, str(infra.id))
    assert second.status_code == 200
    assert fake_ce.get_cost_and_usage.call_count == 1
    assert second.data["cached"] is True
    assert first.data["apps"] == second.data["apps"]


def test_cache_rows_from_a_previous_day_are_pruned(factory, make_infra, make_app, fake_ce):
    from api.models import CostReport

    infra = make_infra(compute_type="ecs_fargate", is_mock=False)
    make_app(infra, name="my-app")

    stale = CostReport.objects.create(
        infrastructure=infra,
        window_start=timezone.now().date() - timedelta(days=100),
        window_end=timezone.now().date() - timedelta(days=70),
        computed_on=timezone.now().date() - timedelta(days=70),
        payload={"apps": [], "shared": {"amount_usd": 0, "source": "actual"}},
    )

    _get_costs(factory, infra.user, str(infra.id))

    assert not CostReport.objects.filter(id=stale.id).exists()


def test_access_denied_maps_to_policy_refresh_required_422(factory, make_infra, make_app, monkeypatch):
    infra = make_infra(compute_type="ecs_fargate", is_mock=False)
    make_app(infra, name="my-app")

    ce = MagicMock()
    ce.get_cost_and_usage.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "GetCostAndUsage",
    )
    monkeypatch.setattr("api.services.cost_service._ce_client", lambda infra: ce)

    resp = _get_costs(factory, infra.user, str(infra.id))

    assert resp.status_code == 422
    assert resp.data["code"] == "policy_refresh_required"


def test_activation_failure_in_org_member_account_is_reported_not_raised(factory, make_infra, make_app, monkeypatch):
    infra = make_infra(compute_type="ecs_fargate", is_mock=False)
    make_app(infra, name="my-app")

    ce = MagicMock()
    ce.get_cost_and_usage.return_value = _ce_response()
    ce.update_cost_allocation_tags_status.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "payer only"}}, "UpdateCostAllocationTagsStatus",
    )
    monkeypatch.setattr("api.services.cost_service._ce_client", lambda infra: ce)

    resp = _get_costs(factory, infra.user, str(infra.id))

    assert resp.status_code == 200
    assert resp.data["tag_activation"]["activated"] is False
    assert resp.data["tag_activation"]["reason"] == "payer_account_required"


# ── window bounds ────────────────────────────────────────────────────────────────

def test_months_out_of_bounds_is_rejected(factory, make_infra, make_app):
    infra = make_infra(is_mock=True)
    make_app(infra)
    resp = _get_costs(factory, infra.user, str(infra.id), months=99)
    assert resp.status_code == 400


def test_months_non_integer_is_rejected(factory, make_infra, make_app):
    infra = make_infra(is_mock=True)
    make_app(infra)
    resp = _get_costs(factory, infra.user, str(infra.id), months="abc")
    assert resp.status_code == 400


# ── rate budget wiring (F0 pattern) ────────────────────────────────────────────────

def test_over_budget_returns_429_before_touching_the_service(factory, make_infra, monkeypatch):
    infra = make_infra(is_mock=True)
    calls = []
    monkeypatch.setattr("api.services.cost_service.CostService.get_costs", lambda *a, **k: calls.append(1))
    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", lambda *a, **k: 45)

    resp = _get_costs(factory, infra.user, str(infra.id))

    assert resp.status_code == 429
    assert resp["Retry-After"] == "45"
    assert calls == []


def test_returns_503_when_limiter_unavailable(factory, make_infra, monkeypatch):
    from shared.errors.exception import HttpError

    def _down(*args, **kwargs):
        raise HttpError("Rate limiter unavailable", status_code=503)

    monkeypatch.setattr("shared.ratelimit.budget.customer_call_budget", _down)
    infra = make_infra(is_mock=True)

    resp = _get_costs(factory, infra.user, str(infra.id))
    assert resp.status_code == 503
