import logging

import redis.asyncio as redis
from constants import EXEMPT_PATHS
from fastapi import HTTPException, Request

from app.core.config import settings

logger = logging.getLogger("api.rate_limiter")

_pool = redis.ConnectionPool.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=20,
)


def _client_ip(request: Request) -> str:
    # Behind N trusted proxies, the immediate peer is the proxy, not the user — read the client
    # from X-Forwarded-For (each hop appends the IP it received from) so users don't share a bucket.
    hops = settings.RATE_LIMIT_TRUSTED_PROXY_HOPS
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self):
        self.redis = redis.Redis(connection_pool=_pool)

    async def check_rate_limit(self, request: Request):
        if request.url.path in EXEMPT_PATHS:
            return

        key = f"rate_limit:{_client_ip(request)}"
        try:
            async with self.redis.pipeline() as pipe:
                pipe.incr(key)
                pipe.ttl(key)
                count, ttl = await pipe.execute()

            if ttl == -1:
                await self.redis.expire(key, settings.RATE_LIMIT_WINDOW_SECONDS)

            if count > settings.MAX_USER_REQUESTS:
                raise HTTPException(status_code=429, detail="Too Many Requests")

        except HTTPException:
            raise
        except Exception as e:  # fail-open: a rate-limiter bug must never break the gateway
            logger.error(f"Rate limiting error: {e}")

    async def close(self):
        await _pool.aclose()
