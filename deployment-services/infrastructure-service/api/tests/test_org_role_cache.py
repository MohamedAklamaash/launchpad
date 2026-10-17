"""shared.services.org_role_cache — security review follow-ups H2 (per-service key
prefix), L4 (only an OrgRole value is ever trusted back out of the cache), and L5
(short, no-retry Redis timeouts so a blackholed Redis degrades fast to the DB).
"""


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


def test_cache_key_is_prefixed_by_service_name(settings):
    from shared.services.org_role_cache import _cache_key

    settings.SERVICE_NAME = "infrastructure-service"
    infra_key = _cache_key("org-1", "account-1")
    settings.SERVICE_NAME = "application-service"
    app_key = _cache_key("org-1", "account-1")

    assert infra_key != app_key
    assert "infrastructure-service" in infra_key
    assert "application-service" in app_key


def test_a_stale_write_from_another_service_name_does_not_poison_this_services_read(
    settings, monkeypatch,
):
    """The concrete H2 attack: service B (lagging behind service A's Membership
    mirror) writes a stale role into what would have been a SHARED key before this
    fix. With per-service prefixes, B's write lands at its own key and is simply
    invisible to A."""
    from shared.services.org_role_cache import get_cached_role, set_cached_role

    fake = _FakeRedis()
    monkeypatch.setattr("shared.services.org_role_cache._redis", lambda: fake)

    settings.SERVICE_NAME = "application-service"
    set_cached_role("org-1", "account-1", "admin")  # service B's (stale) write

    settings.SERVICE_NAME = "infrastructure-service"
    hit, role = get_cached_role("org-1", "account-1")  # service A's read

    assert hit is False
    assert role is None


def test_a_cached_value_outside_orgrole_is_treated_as_a_miss(settings, monkeypatch):
    from shared.services.org_role_cache import _cache_key, get_cached_role

    fake = _FakeRedis()
    monkeypatch.setattr("shared.services.org_role_cache._redis", lambda: fake)
    settings.SERVICE_NAME = "infrastructure-service"

    fake.store[_cache_key("org-1", "account-1")] = "super_admin"  # not a real OrgRole

    hit, role = get_cached_role("org-1", "account-1")

    assert hit is False
    assert role is None


def test_the_none_sentinel_still_resolves_as_a_genuine_negative_cache_hit(settings, monkeypatch):
    from shared.services.org_role_cache import get_cached_role, set_cached_role

    fake = _FakeRedis()
    monkeypatch.setattr("shared.services.org_role_cache._redis", lambda: fake)
    settings.SERVICE_NAME = "infrastructure-service"

    set_cached_role("org-1", "account-1", None)
    hit, role = get_cached_role("org-1", "account-1")

    assert hit is True
    assert role is None


def test_redis_pool_uses_short_no_retry_timeouts():
    from shared.services.org_role_cache import _pool

    opts = _pool.connection_kwargs
    assert opts["socket_timeout"] <= 0.5
    assert opts["socket_connect_timeout"] <= 0.5
    assert opts["retry_on_timeout"] is False
