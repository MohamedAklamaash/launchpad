"""Shared per-user budget helper (F0): fixed-window limit/Retry-After, independent
buckets and users, fail-closed on a Redis error, and the DRF decorator wiring."""
from types import SimpleNamespace

import pytest
import redis
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.errors.exception import HttpError
from shared.ratelimit.budget import (
    acquire_concurrency_slot,
    customer_call_budget,
    rate_limited,
    release_concurrency_slot,
)


class _FakeRedis:
    """In-memory stand-in for the one redis-py surface customer_call_budget/
    acquire_concurrency_slot use (pipelined INCR+TTL, EXPIRE on a fresh key, DECR,
    DELETE) — no fakeredis dependency exists in this codebase, so tests mock the client
    the same way api/tests/test_database_api.py mocks InfraQueue."""

    def __init__(self):
        self.counts = {}
        self.expiry = {}

    def pipeline(self):
        return _FakePipeline(self)

    def expire(self, key, seconds):
        self.expiry[key] = seconds

    def decr(self, key):
        self.counts[key] = self.counts.get(key, 0) - 1
        return self.counts[key]

    def delete(self, key):
        self.counts.pop(key, None)
        self.expiry.pop(key, None)


class _FakePipeline:
    def __init__(self, store):
        self.store = store
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    def ttl(self, key):
        self.ops.append(("ttl", key))
        return self

    def execute(self):
        results = []
        for op, key in self.ops:
            if op == "incr":
                self.store.counts[key] = self.store.counts.get(key, 0) + 1
                results.append(self.store.counts[key])
            else:
                results.append(self.store.expiry.get(key, -1))
        return results

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("shared.ratelimit.budget._redis", lambda: fake)
    return fake


# ── customer_call_budget ─────────────────────────────────────────────────────────

def test_under_limit_is_allowed(fake_redis):
    for _ in range(3):
        assert customer_call_budget("user-1", "databases", limit=3, window=60) is None


def test_over_limit_returns_retry_after(fake_redis):
    for _ in range(2):
        customer_call_budget("user-1", "databases", limit=2, window=60)
    retry_after = customer_call_budget("user-1", "databases", limit=2, window=60)
    assert retry_after == 60


def test_buckets_are_independent(fake_redis):
    for _ in range(2):
        customer_call_budget("user-1", "databases", limit=2, window=60)
    assert customer_call_budget("user-1", "databases", limit=2, window=60) is not None
    assert customer_call_budget("user-1", "evidence", limit=2, window=60) is None


def test_users_are_independent(fake_redis):
    for _ in range(2):
        customer_call_budget("user-1", "databases", limit=2, window=60)
    assert customer_call_budget("user-1", "databases", limit=2, window=60) is not None
    assert customer_call_budget("user-2", "databases", limit=2, window=60) is None


def test_redis_unavailable_raises_503(monkeypatch):
    def _boom():
        raise redis.exceptions.ConnectionError("no route to redis")

    monkeypatch.setattr("shared.ratelimit.budget._redis", _boom)
    with pytest.raises(HttpError) as exc_info:
        customer_call_budget("user-1", "databases", limit=10, window=60)
    assert exc_info.value.status_code == 503


# ── rate_limited decorator ───────────────────────────────────────────────────────

@api_view(['GET'])
@rate_limited('test-bucket', limit=1, window=60)
def _dummy_view(request):
    return Response({'ok': True})


def _authed_get(user_id):
    request = APIRequestFactory().get('/dummy/')
    force_authenticate(request, user=SimpleNamespace(id=user_id))
    return request


def test_decorator_allows_then_blocks_with_retry_after(fake_redis):
    resp1 = _dummy_view(_authed_get("user-a"))
    assert resp1.status_code == 200

    resp2 = _dummy_view(_authed_get("user-a"))
    assert resp2.status_code == 429
    assert resp2["Retry-After"] == "60"


def test_decorator_returns_503_when_redis_down(monkeypatch):
    def _boom():
        raise redis.exceptions.ConnectionError("no route to redis")

    monkeypatch.setattr("shared.ratelimit.budget._redis", _boom)
    resp = _dummy_view(_authed_get("user-b"))
    assert resp.status_code == 503


# ── acquire_concurrency_slot / release_concurrency_slot (F7 ticket 7) ────────────

def test_concurrency_slot_acquired_under_limit(fake_redis):
    assert acquire_concurrency_slot("builds:infra-1", limit=2, ttl=60) is True
    assert acquire_concurrency_slot("builds:infra-1", limit=2, ttl=60) is True


def test_concurrency_slot_refused_at_limit_and_nothing_left_incremented(fake_redis):
    acquire_concurrency_slot("builds:infra-1", limit=1, ttl=60)

    assert acquire_concurrency_slot("builds:infra-1", limit=1, ttl=60) is False
    # The refused attempt must not have left a dangling increment behind — a slot it
    # never actually got must not count against the next caller either.
    assert fake_redis.counts["concurrency:builds:infra-1"] == 1


def test_concurrency_slots_are_independent_per_key(fake_redis):
    acquire_concurrency_slot("builds:infra-1", limit=1, ttl=60)

    assert acquire_concurrency_slot("builds:infra-1", limit=1, ttl=60) is False
    assert acquire_concurrency_slot("builds:infra-2", limit=1, ttl=60) is True


def test_release_frees_the_slot_for_the_next_acquire(fake_redis):
    acquire_concurrency_slot("builds:infra-1", limit=1, ttl=60)
    assert acquire_concurrency_slot("builds:infra-1", limit=1, ttl=60) is False

    release_concurrency_slot("builds:infra-1")

    assert acquire_concurrency_slot("builds:infra-1", limit=1, ttl=60) is True


def test_release_deletes_the_key_once_the_count_reaches_zero(fake_redis):
    acquire_concurrency_slot("builds:infra-1", limit=3, ttl=60)
    release_concurrency_slot("builds:infra-1")

    assert "concurrency:builds:infra-1" not in fake_redis.counts


def test_release_is_safe_to_call_without_a_matching_acquire(fake_redis):
    """The acquire-side backstop TTL can already have expired the key — a release
    arriving after that must not leave the count negative forever."""
    release_concurrency_slot("builds:infra-1")

    assert "concurrency:builds:infra-1" not in fake_redis.counts


def test_acquire_raises_503_when_redis_down(monkeypatch):
    def _boom():
        raise redis.exceptions.ConnectionError("no route to redis")

    monkeypatch.setattr("shared.ratelimit.budget._redis", _boom)
    with pytest.raises(HttpError) as exc_info:
        acquire_concurrency_slot("builds:infra-1", limit=2, ttl=60)
    assert exc_info.value.status_code == 503


def test_release_never_raises_when_redis_down(monkeypatch):
    def _boom():
        raise redis.exceptions.ConnectionError("no route to redis")

    monkeypatch.setattr("shared.ratelimit.budget._redis", _boom)
    release_concurrency_slot("builds:infra-1")  # must not raise
