import logging
import socket

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

# F9 ticket 5 (plan/F9-teams-and-orgs.md, Decision: JWT and authorization): role
# staleness on a Membership change/removal is bounded by the smaller of (this TTL,
# event-propagation latency) — the eviction call in every org.events consumer makes the
# common case sub-second; this TTL is the backstop for a missed/delayed eviction.
CACHE_TTL_SECONDS = 60

# Negative-caching sentinel: a cache HIT whose value is this string means "as of the
# last DB read, this account has no active role in this org" — stored so a non-member
# repeatedly probing (or legitimately polling) an org they don't belong to doesn't
# force a Membership query on every single request. Never confused with a real role
# value, since no OrgRole choice is ever this string.
_NONE_SENTINEL = "__none__"

# Mirrors shared.ratelimit.budget's pool settings — short timeouts plus
# retry_on_timeout so a slow Redis degrades into the DB-fallback path below instead of
# hanging the request.
_pool = redis.ConnectionPool(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    db=settings.REDIS_DB,
    decode_responses=True,
    max_connections=20,
    socket_timeout=5,
    socket_connect_timeout=5,
    socket_keepalive=True,
    socket_keepalive_options={
        socket.TCP_KEEPIDLE: 60,
        socket.TCP_KEEPINTVL: 10,
        socket.TCP_KEEPCNT: 5,
    },
    retry_on_timeout=True,
    health_check_interval=30,
)


def _redis():
    return redis.Redis(connection_pool=_pool)


def _cache_key(org_id, account_id) -> str:
    return f"org_role:{org_id}:{account_id}"


def get_cached_role(org_id, account_id):
    """Returns `(hit, role)`. `hit=False` means "no usable cache entry" — a genuine
    miss OR a Redis error — and the caller must go to the DB; it is never read as
    "deny" or "allow" on its own. `hit=True, role=None` is the negative-caching
    sentinel: a cached "this account has no active role here", distinct from "I don't
    know".

    Fail-closed per ticket scope: unlike `shared.ratelimit.budget.customer_call_budget`
    (which raises 503 on a Redis outage because an AssumeRole-backed call must not run
    unmetered), role resolution's fail-closed posture is "never fall back to allow" —
    achieved here by falling back to the authoritative DB read, not by blocking the
    request.
    """
    try:
        value = _redis().get(_cache_key(org_id, account_id))
    except redis.RedisError:
        logger.warning("org_role cache read failed — falling back to the database", exc_info=True)
        return False, None
    if value is None:
        return False, None
    return True, None if value == _NONE_SENTINEL else value


def set_cached_role(org_id, account_id, role) -> None:
    """Best-effort. A write failure must never raise past a request that already
    computed its answer from the authoritative DB read."""
    try:
        _redis().set(_cache_key(org_id, account_id), role or _NONE_SENTINEL, ex=CACHE_TTL_SECONDS)
    except redis.RedisError:
        logger.warning("org_role cache write failed", exc_info=True)


def evict_cached_role(org_id, account_id) -> None:
    """Best-effort — called by every org.events consumer right after it durably applies
    a Membership/Organization write, so the cache never serves a role this account no
    longer (or newly) has beyond the TTL backstop above. A failure here must never
    nack/fail the event that triggered it — see each consumer's callback."""
    if not org_id or not account_id:
        return
    try:
        _redis().delete(_cache_key(org_id, account_id))
    except redis.RedisError:
        logger.warning("org_role cache eviction failed", exc_info=True)
