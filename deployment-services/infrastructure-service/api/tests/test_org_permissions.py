"""OrgPermissions.get_role — F9 ticket 5's authorization core
(plan/F9-teams-and-orgs.md, Decision: JWT and authorization).

Covers: role resolution for all four roles, a tombstoned membership never resolving to
a role, the org_id claim being checked against the token rather than trusted as a
lookup key, the pre-F9/no-account_id personal-org fallback, cache hit/miss/eviction,
negative caching, Redis-down falling back to the database (never to "allow"), and the
ORGS_ENABLED flag gating the whole feature.
"""
import uuid
from types import SimpleNamespace

import pytest
import redis


class _FakeRedis:
    """Mirrors api/tests/test_rate_budget.py's _FakeRedis — no fakeredis dependency in
    this codebase, so tests stand in for the one surface org_role_cache uses
    (GET/SET/DELETE with an expiry)."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("shared.services.org_role_cache._redis", lambda: fake)
    return fake


@pytest.fixture
def org_and_owner(db):
    from api.models.organization import Organization

    org_id = uuid.uuid4()
    owner_account_id = uuid.uuid4()
    Organization.objects.create(id=org_id, owner_account_id=owner_account_id, version=1)
    return org_id, owner_account_id


def _membership(org_id, account_id, role, *, removed=False):
    from api.models.membership import Membership

    kwargs = {"organization_id": org_id, "account_id": account_id, "role": role, "version": 1}
    if removed:
        from django.utils import timezone
        kwargs["removed_at"] = timezone.now()
    return Membership.objects.create(**kwargs)


def _user(org_id=None, account_id=None, sub=None):
    return SimpleNamespace(id=sub or str(uuid.uuid4()), org_id=org_id, account_id=account_id)


# ── flag off ──────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_flag_off_returns_none_even_for_an_active_owner_membership(settings, org_and_owner):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = False
    org_id, owner_account_id = org_and_owner
    _membership(org_id, owner_account_id, "owner")
    user = _user(org_id=str(org_id), account_id=str(owner_account_id))

    assert OrgPermissions.get_role(user, org_id) is None


# ── role resolution for all four roles ───────────────────────────────────────────

@pytest.mark.django_db
@pytest.mark.parametrize("role", ["owner", "admin", "developer", "viewer"])
def test_resolves_each_role_from_an_active_membership(settings, fake_redis, role):
    from api.models.organization import Organization
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id = uuid.uuid4()
    Organization.objects.create(id=org_id, owner_account_id=uuid.uuid4(), version=1)
    account_id = uuid.uuid4()
    _membership(org_id, account_id, role)
    user = _user(org_id=str(org_id), account_id=str(account_id))

    assert OrgPermissions.get_role(user, org_id) == role


@pytest.mark.django_db
def test_no_membership_resolves_to_none(settings, fake_redis, org_and_owner):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id, _owner = org_and_owner
    user = _user(org_id=str(org_id), account_id=str(uuid.uuid4()))

    assert OrgPermissions.get_role(user, org_id) is None


@pytest.mark.django_db
def test_tombstoned_membership_never_resolves_to_a_role(settings, fake_redis, org_and_owner):
    """A member removed while holding a token scoped to this org must deny, not grant
    their last-known role — the exact race the security review calls out."""
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id, _owner = org_and_owner
    account_id = uuid.uuid4()
    _membership(org_id, account_id, "admin", removed=True)
    user = _user(org_id=str(org_id), account_id=str(account_id))

    assert OrgPermissions.get_role(user, org_id) is None


# ── org_id is a scope check, not a trusted lookup key ────────────────────────────

@pytest.mark.django_db
def test_org_id_argument_must_match_the_tokens_own_org_id_claim(settings, fake_redis):
    """A caller's token is scoped to org A; passing org B's id into get_role must not
    resolve a role there even if the caller happens to also be a real member of B —
    the claim is what the request is authorized for, not a hint."""
    from api.models.organization import Organization
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    Organization.objects.create(id=org_a, owner_account_id=uuid.uuid4(), version=1)
    Organization.objects.create(id=org_b, owner_account_id=uuid.uuid4(), version=1)
    account_id = uuid.uuid4()
    _membership(org_a, account_id, "owner")
    _membership(org_b, account_id, "owner")  # caller really is also a member of B

    user = _user(org_id=str(org_a), account_id=str(account_id))

    assert OrgPermissions.get_role(user, org_b) is None
    assert OrgPermissions.get_role(user, org_a) == "owner"


@pytest.mark.django_db
def test_no_org_id_claim_at_all_resolves_to_none(settings, fake_redis, org_and_owner):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id, owner_account_id = org_and_owner
    _membership(org_id, owner_account_id, "owner")
    user = _user(org_id=None, account_id=str(owner_account_id))

    assert OrgPermissions.get_role(user, org_id) is None


@pytest.mark.django_db
def test_jwt_role_and_roles_claims_are_never_trusted(settings, fake_redis, org_and_owner):
    """A token carrying a stale/forged-looking `role: super_admin` legacy claim with no
    actual Membership must still resolve to None — get_role never reads those claims."""
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id, _owner = org_and_owner
    account_id = uuid.uuid4()  # deliberately NOT given a membership
    user = SimpleNamespace(
        id=str(uuid.uuid4()),
        org_id=str(org_id),
        account_id=str(account_id),
        role="super_admin",
        roles={str(org_id): "owner"},
    )

    assert OrgPermissions.get_role(user, org_id) is None


# ── no account_id claim: always None (security review M1) ───────────────────────
#
# Earlier revisions resolved a claim-less token's role for its own personal org via
# the Organization mirror's owner_account_id. Removed: it broke under ticket 8's
# ownership transfer (a stale/claim-less token would keep resolving the OLD owner's
# role through this path). A token with no account_id claim now always resolves to
# None, personal org or not — the caller must re-login/refresh to get one re-minted.

@pytest.mark.django_db
def test_missing_account_id_resolves_to_none_even_for_the_callers_own_personal_org(
    settings, fake_redis,
):
    from api.models.organization import Organization
    from api.services.org_permissions import OrgPermissions
    from shared.utils.org_id import personal_org_id

    settings.ORGS_ENABLED = True
    legacy_user_id = uuid.uuid4()
    org_id = personal_org_id(legacy_user_id)
    owner_account_id = uuid.uuid4()
    Organization.objects.create(id=org_id, owner_account_id=owner_account_id, version=1)
    _membership(org_id, owner_account_id, "owner")

    user = _user(org_id=str(org_id), account_id=None, sub=str(legacy_user_id))

    assert OrgPermissions.get_role(user, org_id) is None


@pytest.mark.django_db
def test_missing_account_id_resolves_to_none_for_any_other_org_too(settings, fake_redis):
    from api.models.organization import Organization
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    someone_elses_org = uuid.uuid4()
    someone_elses_owner = uuid.uuid4()
    Organization.objects.create(id=someone_elses_org, owner_account_id=someone_elses_owner, version=1)
    _membership(someone_elses_org, someone_elses_owner, "owner")

    user = _user(org_id=str(someone_elses_org), account_id=None, sub=str(uuid.uuid4()))

    assert OrgPermissions.get_role(user, someone_elses_org) is None


# ── UUID canonicalization (security review L1) ──────────────────────────────────

@pytest.mark.django_db
def test_get_role_canonicalizes_a_differently_cased_org_id_argument(settings, fake_redis, org_and_owner):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id, owner_account_id = org_and_owner
    _membership(org_id, owner_account_id, "owner")
    user = _user(org_id=str(org_id), account_id=str(owner_account_id))

    assert OrgPermissions.get_role(user, str(org_id).upper()) == "owner"


@pytest.mark.django_db
def test_get_role_canonicalizes_a_differently_cased_token_org_id_claim(settings, fake_redis, org_and_owner):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id, owner_account_id = org_and_owner
    _membership(org_id, owner_account_id, "owner")
    user = _user(org_id=str(org_id).upper(), account_id=str(owner_account_id))

    assert OrgPermissions.get_role(user, org_id) == "owner"


@pytest.mark.django_db
def test_get_role_returns_none_for_a_malformed_org_id_argument(settings, fake_redis):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    user = _user(org_id="not-a-uuid", account_id=str(uuid.uuid4()))

    assert OrgPermissions.get_role(user, "not-a-uuid") is None


# ── cache hit / miss / eviction ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_cache_miss_then_hit_skips_the_second_database_query(settings, fake_redis, monkeypatch, org_and_owner):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id, owner_account_id = org_and_owner
    _membership(org_id, owner_account_id, "owner")
    user = _user(org_id=str(org_id), account_id=str(owner_account_id))

    calls = {"n": 0}
    original = OrgPermissions._role_from_db

    def _counting(org_id_, account_id_):
        calls["n"] += 1
        return original(org_id_, account_id_)

    monkeypatch.setattr(OrgPermissions, "_role_from_db", staticmethod(_counting))

    assert OrgPermissions.get_role(user, org_id) == "owner"
    assert calls["n"] == 1

    assert OrgPermissions.get_role(user, org_id) == "owner"
    assert calls["n"] == 1  # second call served from cache, no DB hit


@pytest.mark.django_db
def test_negative_cache_remembers_a_non_member_without_hammering_the_db(
    settings, fake_redis, monkeypatch, org_and_owner,
):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id, _owner = org_and_owner
    account_id = uuid.uuid4()  # never a member
    user = _user(org_id=str(org_id), account_id=str(account_id))

    calls = {"n": 0}
    original = OrgPermissions._role_from_db

    def _counting(org_id_, account_id_):
        calls["n"] += 1
        return original(org_id_, account_id_)

    monkeypatch.setattr(OrgPermissions, "_role_from_db", staticmethod(_counting))

    assert OrgPermissions.get_role(user, org_id) is None
    assert OrgPermissions.get_role(user, org_id) is None
    assert calls["n"] == 1


@pytest.mark.django_db
def test_evict_clears_the_cached_role_so_the_next_read_hits_the_database(
    settings, fake_redis, org_and_owner,
):
    from api.services.org_permissions import OrgPermissions

    settings.ORGS_ENABLED = True
    org_id, owner_account_id = org_and_owner
    membership = _membership(org_id, owner_account_id, "admin")
    user = _user(org_id=str(org_id), account_id=str(owner_account_id))

    assert OrgPermissions.get_role(user, org_id) == "admin"

    # Role changes underneath the cache (e.g. a demotion) — without eviction, the
    # cached "admin" would still be served for up to the TTL.
    membership.role = "viewer"
    membership.save(update_fields=["role"])

    OrgPermissions.evict(org_id, owner_account_id)

    assert OrgPermissions.get_role(user, org_id) == "viewer"


# ── Redis down: fail closed means "fall back to the DB", never "allow" ──────────

@pytest.mark.django_db
def test_redis_down_on_read_falls_back_to_the_database_not_to_allow(
    settings, monkeypatch, org_and_owner,
):
    from api.services.org_permissions import OrgPermissions

    def _boom():
        raise redis.exceptions.ConnectionError("no route to redis")

    monkeypatch.setattr("shared.services.org_role_cache._redis", _boom)

    settings.ORGS_ENABLED = True
    org_id, owner_account_id = org_and_owner
    _membership(org_id, owner_account_id, "owner")
    user = _user(org_id=str(org_id), account_id=str(owner_account_id))

    # Correct answer still comes back — from the database, not a permissive default.
    assert OrgPermissions.get_role(user, org_id) == "owner"

    # And a genuine non-member still correctly gets denied, not waved through.
    stranger = _user(org_id=str(org_id), account_id=str(uuid.uuid4()))
    assert OrgPermissions.get_role(stranger, org_id) is None


@pytest.mark.django_db
def test_redis_down_on_write_never_raises(settings, monkeypatch, org_and_owner):
    from api.services.org_permissions import OrgPermissions

    def _boom():
        raise redis.exceptions.ConnectionError("no route to redis")

    monkeypatch.setattr("shared.services.org_role_cache._redis", _boom)

    settings.ORGS_ENABLED = True
    org_id, owner_account_id = org_and_owner
    _membership(org_id, owner_account_id, "owner")
    user = _user(org_id=str(org_id), account_id=str(owner_account_id))

    # Must not raise despite every cache operation failing.
    assert OrgPermissions.get_role(user, org_id) == "owner"
    OrgPermissions.evict(org_id, owner_account_id)
