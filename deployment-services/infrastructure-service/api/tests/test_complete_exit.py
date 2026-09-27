"""F6 'Complete exit': the owner-only, confirmation-gated action that requests platform
DNS teardown (F1b's real `request_and_await_dns_teardown`, not a stub — merged to main
before this feature) and marks an infrastructure exited. Read-only export stays separate;
this is the one action that changes anything, and only in Launchpad's own platform DNS
zone, never the customer's AWS account."""
import time
import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.utils.jwt import JWTUser


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    class _Pipe:
        def __init__(self, store):
            self.store, self.ops = store, []
        def incr(self, key):
            self.ops.append(("incr", key)); return self
        def ttl(self, key):
            self.ops.append(("ttl", key)); return self
        def execute(self):
            results = []
            for op, key in self.ops:
                if op == "incr":
                    self.store.counts[key] = self.store.counts.get(key, 0) + 1
                    results.append(self.store.counts[key])
                else:
                    results.append(self.store.expiry.get(key, -1))
            return results
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Redis:
        def __init__(self):
            self.counts, self.expiry = {}, {}
        def pipeline(self):
            return _Pipe(self)
        def expire(self, key, seconds):
            self.expiry[key] = seconds

    fake = _Redis()
    monkeypatch.setattr("shared.ratelimit.budget._redis", lambda: fake)
    return fake


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

    def _make(*, owner=None):
        owner = owner or make_user()
        infra = Infrastructure.objects.create(
            user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, code="123456789012", metadata={"aws_region": "us-east-1"},
            dns_label=uuid.uuid4().hex[:16],
        )
        return owner, infra
    return _make


def _jwt_user(user, *, auth_time=None):
    payload = {"sub": str(user.id)}
    if auth_time is not None:
        payload["auth_time"] = auth_time
    return JWTUser(**payload)


def _fresh_auth_time():
    return int(time.time())


def _post(factory, user, infra_id, body=None):
    from api.views.exit_export import infrastructure_complete_exit

    request = factory.post(f"/api/v1/infrastructures/{infra_id}/exit/", data=body or {}, format="json")
    force_authenticate(request, user=user)
    return infrastructure_complete_exit(request, infra_id=infra_id)


@patch("api.views.exit_export.request_and_await_dns_teardown")
def test_owner_completes_exit(mock_teardown, factory, make_infra):
    owner, infra = make_infra()
    mock_teardown.return_value = None

    resp = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})

    assert resp.status_code == 200
    assert resp.data["status"] == "exited"
    infra.refresh_from_db()
    assert infra.exited_at is not None
    mock_teardown.assert_called_once()


def test_requires_confirm_true(factory, make_infra):
    owner, infra = make_infra()
    resp = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": False})
    assert resp.status_code == 400
    infra.refresh_from_db()
    assert infra.exited_at is None


def test_invited_admin_gets_403(factory, make_infra, make_user):
    _owner, infra = make_infra()
    invited = make_user()
    infra.invited_users.add(invited)
    resp = _post(factory, _jwt_user(invited, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})
    assert resp.status_code == 403


def test_stranger_gets_404(factory, make_infra, make_user):
    _owner, infra = make_infra()
    resp = _post(factory, _jwt_user(make_user(), auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})
    assert resp.status_code == 404


def test_stale_auth_time_gets_401(factory, make_infra):
    owner, infra = make_infra()
    stale = int(time.time()) - 900
    resp = _post(factory, _jwt_user(owner, auth_time=stale), str(infra.id), {"confirm": True})
    assert resp.status_code == 401
    assert resp.data["code"] == "reauth_required"


@patch("api.views.exit_export.request_and_await_dns_teardown")
def test_already_exited_is_409(mock_teardown, factory, make_infra):
    owner, infra = make_infra()
    mock_teardown.return_value = None
    first = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})
    assert first.status_code == 200

    second = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})
    assert second.status_code == 409


@patch("api.views.exit_export.request_and_await_dns_teardown")
def test_dns_teardown_pending_does_not_mark_exited(mock_teardown, factory, make_infra):
    from api.services.platform_dns.teardown import DnsTeardownPending

    owner, infra = make_infra()
    mock_teardown.side_effect = DnsTeardownPending("not confirmed")

    resp = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})

    assert resp.status_code == 202
    infra.refresh_from_db()
    assert infra.exited_at is None


@patch("api.views.exit_export.request_and_await_dns_teardown")
def test_exited_at_is_exposed_on_the_infrastructure_response(mock_teardown, factory, make_infra):
    """Without this, the dashboard's loadData() never sees exited_at after Complete Exit
    and keeps showing both the Export and Complete Exit actions."""
    from api.serializers.infrastructure import InfrastructureSerializer

    owner, infra = make_infra()
    mock_teardown.return_value = None
    _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})
    infra.refresh_from_db()

    data = InfrastructureSerializer.serialize_instance(infra)
    assert data["exited_at"] is not None


@patch("api.views.exit_export.request_and_await_dns_teardown")
def test_audit_row_written_on_success(mock_teardown, factory, make_infra):
    from api.models.exit_export_access import ExitExportAccess

    owner, infra = make_infra()
    mock_teardown.return_value = None
    _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})

    row = ExitExportAccess.objects.get(infrastructure_id=infra.id, action="complete_exit", status_code=200)
    assert row.user_id == owner.id


@patch("api.views.exit_export.request_and_await_dns_teardown")
def test_per_user_budget_returns_429(mock_teardown, factory, make_infra, settings):
    settings.RATE_BUDGET_COMPLETE_EXIT_LIMIT = 1
    mock_teardown.return_value = None
    owner, infra = make_infra()

    first = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})
    assert first.status_code == 200

    _owner2, infra2 = make_infra(owner=owner)
    second = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra2.id), {"confirm": True})
    assert second.status_code == 429
    assert "Retry-After" in second


def test_redis_unavailable_fails_closed(factory, make_infra, monkeypatch):
    owner, infra = make_infra()

    def _boom():
        raise __import__("redis").RedisError("down")

    monkeypatch.setattr("shared.ratelimit.budget._redis", _boom)
    resp = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})
    assert resp.status_code == 503


@patch("api.views.exit_export.request_and_await_dns_teardown")
def test_unhandled_teardown_error_is_a_500_and_is_audited(mock_teardown, factory, make_infra):
    from api.models.exit_export_access import ExitExportAccess

    mock_teardown.side_effect = RuntimeError("boom")
    owner, infra = make_infra()

    resp = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})

    assert resp.status_code == 500
    infra.refresh_from_db()
    assert infra.exited_at is None
    row = ExitExportAccess.objects.get(infrastructure_id=infra.id, action="complete_exit", status_code=500)
    assert row.user_id == owner.id


@patch("api.views.exit_export.request_and_await_dns_teardown")
def test_complete_exit_tears_down_custom_domains(mock_teardown, factory, make_infra):
    """F1b part 3b: exit is not an infra destroy (the customer keeps their AWS resources),
    but Launchpad stops managing routing for it — every custom domain must be detached and
    have its certificate deleted too."""
    mock_teardown.return_value = None
    owner, infra = make_infra()

    with patch("api.services.custom_domain_service.CustomDomainService.teardown_for_infrastructure") as teardown:
        resp = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})

    assert resp.status_code == 200
    teardown.assert_called_once()
    assert teardown.call_args.args[0].id == infra.id


@patch("api.views.exit_export.request_and_await_dns_teardown")
def test_complete_exit_succeeds_even_if_custom_domain_teardown_fails(mock_teardown, factory, make_infra):
    """Never gates exited_at — a custom-domain teardown failure must not block the
    outcome this endpoint is actually accountable for confirming (the DNS teardown)."""
    mock_teardown.return_value = None
    owner, infra = make_infra()

    with patch("api.services.custom_domain_service.CustomDomainService.teardown_for_infrastructure",
               side_effect=RuntimeError("boom")):
        resp = _post(factory, _jwt_user(owner, auth_time=_fresh_auth_time()), str(infra.id), {"confirm": True})

    assert resp.status_code == 200
    infra.refresh_from_db()
    assert infra.exited_at is not None
