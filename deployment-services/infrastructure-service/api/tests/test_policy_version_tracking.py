"""`Infrastructure.policy_version` — what the customer's account actually has applied.

Without it, widening LaunchpadDeploymentPolicy is a silent failure: nothing tells an
existing customer to re-run the refresh script, and the first symptom is a deploy dying
on AccessDenied. These tests cover the two callbacks that report it and the staleness
signal the dashboard reads.
"""

import uuid

import pytest
from api.cloud_providers.aws import iam_policy
from rest_framework.test import APIRequestFactory


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="tester",
        )
    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(user=None, **kwargs):
        return Infrastructure.objects.create(
            user=user or make_user(),
            name=f"infra-{uuid.uuid4()}",
            cloud_provider="aws",
            max_cpu=1024,
            max_memory=512,
            code="123456789012",
            metadata={},
            **kwargs,
        )
    return _make


@pytest.fixture
def refresh_view():
    from api.views.script_api_key import infrastructure_policy_refresh_callback
    return infrastructure_policy_refresh_callback


def _post_refresh(factory, view, api_key, **payload):
    payload.setdefault("account_id", "123456789012")
    request = factory.post(
        "/api/v1/infrastructures/policy-refresh/callback/", payload,
        format="json", HTTP_X_API_KEY=api_key,
    )
    return view(request)


# ── parsing what the script reports ───────────────────────────────────────────

@pytest.mark.parametrize("raw", [None, "", "abc", "1.5", 0, -3, [], {}])
def test_unusable_reported_versions_are_dropped_not_raised(db, raw):
    """The callbacks carry no JWT and the version is advisory. A malformed value must
    never be the reason an otherwise-valid onboarding fails."""
    from api.models.infrastructure import Infrastructure

    assert Infrastructure.parse_reported_policy_version(raw) is None


def test_reported_version_is_clamped_to_what_launchpad_published(db):
    """A caller claiming a future version would otherwise mark itself current and never
    see the refresh prompt it needs."""
    from api.models.infrastructure import Infrastructure

    assert Infrastructure.parse_reported_policy_version(9999) == iam_policy.version()
    assert Infrastructure.parse_reported_policy_version(str(iam_policy.version())) == iam_policy.version()


# ── staleness signal ──────────────────────────────────────────────────────────

def test_null_policy_version_counts_as_stale(make_infra):
    infra = make_infra()
    assert infra.policy_version is None
    assert infra.policy_refresh_required() is True


def test_current_policy_version_is_not_stale(make_infra):
    infra = make_infra(policy_version=iam_policy.version())
    assert infra.policy_refresh_required() is False


# ── staleness is per compute_type ─────────────────────────────────────────────

def test_ecs_infra_on_v1_is_not_flagged_by_an_eks_only_bump(make_infra, monkeypatch):
    """The entire point of this fix: an EKS-only policy bump must not tell every ECS
    customer their identical, already-applied policy is out of date. Pins
    required_version_for to a synthetic v1(ecs)/v2(eks) state so this stays true
    regardless of what the live policy has bumped to since (e.g. F4's v3, which — unlike
    v2 — does change the base statements and so does move ecs_fargate's requirement)."""
    monkeypatch.setattr(
        iam_policy, "required_version_for", lambda compute_type: {"ecs_fargate": 1, "eks": 2}[compute_type]
    )
    infra = make_infra(compute_type="ecs_fargate", policy_version=1)
    assert infra.policy_refresh_required() is False


def test_eks_infra_on_v1_is_flagged(make_infra, monkeypatch):
    """EKS didn't exist at v1, so an EKS infra reporting v1 cannot hold the EKS grants
    added at v2 — it must be flagged."""
    monkeypatch.setattr(
        iam_policy, "required_version_for", lambda compute_type: {"ecs_fargate": 1, "eks": 2}[compute_type]
    )
    infra = make_infra(compute_type="eks", policy_version=1)
    assert infra.policy_refresh_required() is True


def test_null_policy_version_is_stale_for_every_compute_type(make_infra):
    ecs_infra = make_infra(compute_type="ecs_fargate")
    eks_infra = make_infra(compute_type="eks")
    assert ecs_infra.policy_refresh_required() is True
    assert eks_infra.policy_refresh_required() is True


def test_serializer_exposes_the_version_gap(make_infra):
    from api.serializers.infrastructure import InfrastructureSerializer

    infra = make_infra()
    data = InfrastructureSerializer.serialize_instance(infra)
    assert data["policy_version"] is None
    assert data["current_policy_version"] == iam_policy.version()
    assert data["required_policy_version"] == iam_policy.required_version_for("ecs_fargate")
    assert data["policy_refresh_required"] is True


def test_serializer_required_policy_version_is_per_compute_type(make_infra, monkeypatch):
    """Pins required_version_for to a synthetic v1(ecs)/v2(eks) state — see the
    matching note on test_ecs_infra_on_v1_is_not_flagged_by_an_eks_only_bump."""
    from api.serializers.infrastructure import InfrastructureSerializer

    monkeypatch.setattr(
        iam_policy, "required_version_for", lambda compute_type: {"ecs_fargate": 1, "eks": 2}[compute_type]
    )
    ecs_infra = make_infra(compute_type="ecs_fargate", policy_version=1)
    eks_infra = make_infra(compute_type="eks", policy_version=1)

    ecs_data = InfrastructureSerializer.serialize_instance(ecs_infra)
    eks_data = InfrastructureSerializer.serialize_instance(eks_infra)

    assert ecs_data["required_policy_version"] == 1
    assert ecs_data["policy_refresh_required"] is False
    assert eks_data["required_policy_version"] == 2
    assert eks_data["policy_refresh_required"] is True


# ── policy-refresh callback ───────────────────────────────────────────────────

def test_refresh_callback_records_the_version_on_infra_and_event(
    factory, refresh_view, make_user, make_infra
):
    from api.models.policy_refresh_event import PolicyRefreshEvent
    from api.models.script_api_key import ScriptApiKey

    user = make_user()
    infra = make_infra(user)
    api_key = ScriptApiKey.issue(user)

    response = _post_refresh(
        factory, refresh_view, api_key,
        infra_id=str(infra.id), policy_version=iam_policy.version(),
    )
    assert response.status_code == 201

    infra.refresh_from_db()
    assert infra.policy_version == iam_policy.version()
    event = PolicyRefreshEvent.objects.get(id=response.data["event_id"])
    assert event.policy_version == iam_policy.version()


def test_refresh_callback_from_an_older_script_still_succeeds(
    factory, refresh_view, make_user, make_infra
):
    """Scripts are pinned per-deploy, so a customer can be running a copy predating
    versioning. It must record the refresh, just without a version."""
    from api.models.script_api_key import ScriptApiKey

    user = make_user()
    infra = make_infra(user)
    api_key = ScriptApiKey.issue(user)

    response = _post_refresh(factory, refresh_view, api_key, infra_id=str(infra.id))
    assert response.status_code == 201
    infra.refresh_from_db()
    assert infra.policy_version is None


def test_refresh_callback_never_rewinds_a_recorded_version(
    factory, refresh_view, make_user, make_infra, monkeypatch
):
    """An old pinned copy re-run against an already-current account must not reopen the
    refresh prompt."""
    from api.models.script_api_key import ScriptApiKey

    monkeypatch.setattr(iam_policy, "version", lambda: 5)
    user = make_user()
    infra = make_infra(user, policy_version=5)
    api_key = ScriptApiKey.issue(user)

    response = _post_refresh(
        factory, refresh_view, api_key, infra_id=str(infra.id), policy_version=2,
    )
    assert response.status_code == 201
    infra.refresh_from_db()
    assert infra.policy_version == 5


def test_refresh_callback_reenqueues_provision_when_crossing_the_tls_threshold(
    factory, refresh_view, make_user, make_infra
):
    """R4: a POLICY_STALE certificate never re-evaluates itself on a timer — the refresh
    callback must re-enqueue provisioning once the reported version crosses
    MIN_POLICY_VERSION_FOR_TLS, but only when the environment is already ACTIVE."""
    from unittest.mock import patch

    from api.models.environment import Environment
    from api.models.infrastructure_certificate import InfrastructureCertificate
    from api.models.script_api_key import ScriptApiKey
    from api.services.cert_bootstrap import MIN_POLICY_VERSION_FOR_TLS

    user = make_user()
    infra = make_infra(user, policy_version=MIN_POLICY_VERSION_FOR_TLS - 1)
    Environment.objects.create(infrastructure=infra, status="ACTIVE")
    InfrastructureCertificate.objects.create(
        infrastructure=infra, tls_status=InfrastructureCertificate.TLS_POLICY_STALE,
    )
    api_key = ScriptApiKey.issue(user)

    with patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        response = _post_refresh(
            factory, refresh_view, api_key,
            infra_id=str(infra.id), policy_version=MIN_POLICY_VERSION_FOR_TLS,
        )

    assert response.status_code == 201
    enqueue.assert_called_once_with(str(infra.id))


def test_refresh_callback_does_not_reenqueue_when_environment_is_not_active(
    factory, refresh_view, make_user, make_infra
):
    from unittest.mock import patch

    from api.models.environment import Environment
    from api.models.script_api_key import ScriptApiKey
    from api.services.cert_bootstrap import MIN_POLICY_VERSION_FOR_TLS

    user = make_user()
    infra = make_infra(user, policy_version=MIN_POLICY_VERSION_FOR_TLS - 1)
    Environment.objects.create(infrastructure=infra, status="PENDING")
    api_key = ScriptApiKey.issue(user)

    with patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        response = _post_refresh(
            factory, refresh_view, api_key,
            infra_id=str(infra.id), policy_version=MIN_POLICY_VERSION_FOR_TLS,
        )

    assert response.status_code == 201
    enqueue.assert_not_called()


def test_refresh_callback_does_not_reenqueue_below_the_tls_threshold(
    factory, refresh_view, make_user, make_infra
):
    from unittest.mock import patch

    from api.models.environment import Environment
    from api.models.script_api_key import ScriptApiKey
    from api.services.cert_bootstrap import MIN_POLICY_VERSION_FOR_TLS

    user = make_user()
    infra = make_infra(user, policy_version=1)
    Environment.objects.create(infrastructure=infra, status="ACTIVE")
    api_key = ScriptApiKey.issue(user)

    with patch("api.services.infra_queue.InfraQueue.enqueue_provision") as enqueue:
        response = _post_refresh(
            factory, refresh_view, api_key,
            infra_id=str(infra.id), policy_version=MIN_POLICY_VERSION_FOR_TLS - 1,
        )

    assert response.status_code == 201
    enqueue.assert_not_called()


def test_refresh_callback_does_not_touch_an_infra_the_key_owner_does_not_own(
    factory, refresh_view, make_user, make_infra
):
    from api.models.script_api_key import ScriptApiKey

    victim_infra = make_infra(make_user())
    attacker = make_user()
    api_key = ScriptApiKey.issue(attacker)

    response = _post_refresh(
        factory, refresh_view, api_key,
        infra_id=str(victim_infra.id), policy_version=iam_policy.version(),
    )
    assert response.status_code == 201
    victim_infra.refresh_from_db()
    assert victim_infra.policy_version is None
