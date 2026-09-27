import socket
from functools import wraps

import redis
from django.conf import settings
from rest_framework.response import Response

from shared.errors.exception import HttpError

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


def rate_limited(bucket: str, limit: int, window: int):
    """DRF view decorator enforcing customer_call_budget, keyed on the authenticated
    user resolved by shared.middleware.authentication.JWTAuthMiddleware. Apply directly
    to the view function, underneath @api_view, so it runs before any DB/AWS work."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
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
