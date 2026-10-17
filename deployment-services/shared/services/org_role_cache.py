import logging
import socket

import redis
from django.conf import settings

from shared.enums.org_role import OrgRole

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

# Security review L5: short, no-retry timeouts. This cache sits in front of a DB read
# that's fast and always correct — unlike shared.ratelimit.budget (which retries a
# slow Redis because losing that race means falling back to raising 503), a blackholed
# Redis here should degrade to the DB fallback in well under a request timeout, not
# spend a retry-and-backoff budget hoping Redis comes back first.
_pool = redis.ConnectionPool(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    db=settings.REDIS_DB,
    decode_responses=True,
    max_connections=20,
    socket_timeout=0.5,
    socket_connect_timeout=0.5,
    socket_keepalive=True,
    socket_keepalive_options={
        socket.TCP_KEEPIDLE: 60,
        socket.TCP_KEEPINTVL: 10,
        socket.TCP_KEEPCNT: 5,
    },
    retry_on_timeout=False,
    health_check_interval=30,
)


def _redis():
    return redis.Redis(connection_pool=_pool)


def _cache_key(org_id, account_id) -> str:
    # Security review H2: prefixed per-service. infrastructure-service,
    # application-service, and payment-service each maintain their own Membership
    # mirror, consumed from the same org.events stream but never guaranteed to be at
    # the same version at the same instant (see plan/F9-teams-and-orgs.md's
    # version-rejection rule) — without this prefix, three services sharing one Redis
    # instance would also share one cache entry per (org, account), and whichever
    # service's mirror lagged furthest behind could write its stale answer into a key
    # the other two read from. SERVICE_NAME is a plain per-service Django setting, not
    # derived from anything request-supplied.
    service_name = getattr(settings, "SERVICE_NAME", "unknown-service")
    return f"org_role:{service_name}:{org_id}:{account_id}"


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

    Security review L4: a cached value that isn't the sentinel AND isn't a value
    `OrgRole` actually defines is treated as a miss, not trusted as a role. This is a
    defensive floor against a corrupted/hand-edited Redis key (or a future bug writing
    something unexpected) ever being handed back as if it were a real role — it can
    only ever widen to "go check the DB", never to "grant an unrecognized role".
    """
    try:
        value = _redis().get(_cache_key(org_id, account_id))
    except redis.RedisError:
        logger.warning("org_role cache read failed — falling back to the database", exc_info=True)
        return False, None
    if value is None:
        return False, None
    if value == _NONE_SENTINEL:
        return True, None
    if value not in OrgRole.values:
        logger.warning("org_role cache returned an unrecognized value — treating as a miss")
        return False, None
    return True, value


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
