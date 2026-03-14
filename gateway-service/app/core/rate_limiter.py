import redis.asyncio as redis
from fastapi import Request, HTTPException
from app.core.config import settings
import logging
from constants import EXEMPT_PATHS

logger = logging.getLogger("api.rate_limiter")

_pool = redis.ConnectionPool.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=20,
)


class RateLimiter:
    def __init__(self):
        self.redis = redis.Redis(connection_pool=_pool)

    async def check_rate_limit(self, request: Request):
        if request.url.path in EXEMPT_PATHS:
            return

        key = f"rate_limit:{request.client.host}"
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
        except Exception as e:
            logger.error(f"Rate limiting error: {e}")

    async def close(self):
        await _pool.aclose()
