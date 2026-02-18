import redis.asyncio as redis
from fastapi import Request, HTTPException
from app.core.config import settings
import logging
from constants import EXEMPT_PATHS
logger = logging.getLogger("api.rate_limiter")

class RateLimiter:
    def __init__(self):
        self.redis = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)

    async def check_rate_limit(self, request: Request):
        if request.url.path in EXEMPT_PATHS:
            return

        client_ip = request.client.host
        key = f"rate_limit:{client_ip}"

        try:
            async with self.redis.pipeline() as pipe:
                pipe.incr(key)
                pipe.ttl(key)
                results = await pipe.execute()
                
                count = results[0]
                ttl = results[1]
                
                if ttl == -1:
                    await self.redis.expire(key, settings.RATE_LIMIT_WINDOW_SECONDS)
                
                if count > settings.MAX_USER_REQUESTS:
                    raise HTTPException(status_code=429, detail="Too Many Requests")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Rate limiting error: {e}")
            pass
