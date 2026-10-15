import logging
import socket
from functools import wraps

import redis
from django.conf import settings
from rest_framework.response import Response

from shared.errors.exception import HttpError

logger = logging.getLogger(__name__)

# Mirrors the pool settings in api.services.infra_queue: short timeouts plus
# retry_on_timeout so a slow Redis degrades this into the fail-closed path below
# instead of hanging the request.
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


def customer_call_budget(user_id, bucket: str, limit: int, window: int):
    """Fixed-window per-user budget for an endpoint that calls into the customer's AWS
    account. Buckets are per feature so one feature cannot starve another's budget.

    Returns None if the call is within budget, or the number of seconds until the
    window resets if the caller is over budget.

    Raises HttpError(status_code=503) if Redis is unreachable. These endpoints cost the
    customer an AssumeRole plus at least one call in their own account — an unavailable
    limiter is not a reason to let the call through for free, so this fails closed
    (unlike the gateway's per-IP limiter, which fails open).
    """
    key = f"budget:{bucket}:{user_id}"
    try:
        with _redis().pipeline() as pipe:
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = pipe.execute()
        if ttl == -1:
            _redis().expire(key, window)
            ttl = window
        if count > limit:
            return ttl
        return None
    except redis.RedisError as e:
        raise HttpError(message="Rate limiter unavailable", status_code=503) from e


def acquire_concurrency_slot(key: str, limit: int, ttl: int) -> bool:
    """Redis-backed counting semaphore for a bounded number of concurrent in-flight
    operations sharing `key` (e.g. one infrastructure's concurrent builds, F7 ticket 7).
    Unlike `customer_call_budget`'s fixed window, a slot acquired here must be released
    by the caller (`release_concurrency_slot`) on every exit path — success, failure,
    exception, or timeout — or it leaks until `ttl` expires it as a backstop. Callers
    should set `ttl` comfortably above the longest a held slot can legitimately run
    (e.g. the build timeout plus margin), so the backstop only ever fires for a worker
    that crashed before it could release.

    Returns True if the slot was acquired (the caller now holds it and owes a matching
    release), False if `limit` is already reached (nothing was incremented — no release
    is owed).

    Raises HttpError(status_code=503) if Redis is unreachable — fails closed, same
    posture as `customer_call_budget`: an unavailable limiter is not a reason to let an
    unbounded number of concurrent operations through for free."""
    full_key = f"concurrency:{key}"
    try:
        with _redis().pipeline() as pipe:
            pipe.incr(full_key)
            pipe.ttl(full_key)
            count, ttl_remaining = pipe.execute()
        if ttl_remaining == -1:
            _redis().expire(full_key, ttl)
        if count > limit:
            _redis().decr(full_key)
            return False
        return True
    except redis.RedisError as e:
        raise HttpError(message="Rate limiter unavailable", status_code=503) from e


def release_concurrency_slot(key: str) -> None:
    """Releases a slot acquired by `acquire_concurrency_slot`. Best-effort: Redis being
    unreachable here must not raise past work that already finished — the acquire
    side's backstop TTL is exactly what covers this case. Deletes the key outright
    rather than leaving it negative once the count reaches zero, so a later acquire
    starts from a clean slate instead of counting down from a negative number forever."""
    full_key = f"concurrency:{key}"
    try:
        remaining = _redis().decr(full_key)
        if remaining <= 0:
            _redis().delete(full_key)
    except redis.RedisError as e:
        logger.warning(f"Could not release concurrency slot {full_key}: {e}")


def rate_limited(bucket: str, limit: int, window: int, methods: tuple[str, ...] | None = None):
    """DRF view decorator enforcing customer_call_budget, keyed on the authenticated
    user resolved by shared.middleware.authentication.JWTAuthMiddleware. Apply directly
    to the view function, underneath @api_view, so it runs before any DB/AWS work.

    `methods`, if given, restricts this budget to requests whose method is in the set —
    any other method passes straight through uncharged. This lets a tighter,
    write-specific bucket stack on top of a view's general read+write bucket (H6:
    `databases_write` on top of `databases`) without a GET also consuming the tighter
    budget."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if methods is not None and request.method not in methods:
                return view_func(request, *args, **kwargs)
            try:
                retry_after = customer_call_budget(request.user.id, bucket, limit, window)
            except HttpError as e:
                return Response({"error": e.message}, status=e.status_code)
            if retry_after is not None:
                return Response(
                    {"error": "Too many requests for this operation, try again later"},
                    status=429,
                    headers={"Retry-After": str(retry_after)},
                )
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
