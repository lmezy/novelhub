"""Rate limiting middleware using Redis."""

import time

import redis.asyncio as aioredis

from fastapi import Request, HTTPException
from loguru import logger

from app.core.config import settings


class RateLimitMiddleware:
    """Redis-based rate limiter.

    Limits:
      - /api/auth/* : 10 req/min per IP
      - /api/*       : 200 req/min per IP

    Implemented as a pure ASGI middleware (not BaseHTTPMiddleware)
    to avoid event-loop conflicts with asyncpg connection pools.
    """

    def __init__(self, app):
        self.app = app
        self.redis = aioredis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/1",
            encoding="utf-8", decode_responses=True,
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        # Determine limits
        if path.startswith("/api/auth"):
            limit = 10
        elif path.startswith("/api/"):
            limit = 200
        else:
            await self.app(scope, receive, send)
            return

        # Get client IP
        client_ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:{client_ip}:{path.split('/')[2] if len(path.split('/')) > 2 else 'root'}"

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

        await self.app(scope, receive, send)
