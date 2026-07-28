"""Rate limiting middleware using Redis."""

import time
import redis.asyncio as aioredis

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger

from app.core.config import settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-based rate limiter.

    Limits:
      - /api/auth/* : 10 req/min per IP
      - /api/*       : 200 req/min per IP
    """

    def __init__(self, app):
        super().__init__(app)
        self.redis = aioredis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/1",
            encoding="utf-8", decode_responses=True,
        )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Determine limits
        if path.startswith("/api/auth"):
            limit = 10
        elif path.startswith("/api/"):
            limit = 200
        else:
            return await call_next(request)

        # Get client IP
        client_ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:{client_ip}:{path.split(chr(47))[2] if len(path.split(chr(47))) > 2 else chr(114)+chr(111)+chr(111)+chr(116)}"

        try:
            current = await self.redis.get(key)
            if current and int(current) >= limit:
                logger.warning("Rate limit hit: {} on {}", client_ip, path)
                raise HTTPException(status_code=429, detail="Too many requests")

            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 60)
            await pipe.execute()
        except HTTPException:
            raise
        except Exception:
            # Redis unavailable, allow request
            pass

        return await call_next(request)
