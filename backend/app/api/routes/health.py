"""System health check endpoint."""

import shutil

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health_check():
    return {"status": "running", "project": settings.PROJECT_NAME}


@router.get("/detailed")
async def detailed_health(db: AsyncSession = Depends(get_db)):
    result = {"project": settings.PROJECT_NAME, "checks": {}}

    # PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        result["checks"]["postgres"] = "ok"
    except Exception as e:
        result["checks"]["postgres"] = f"error: {e}"

    # Redis
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}")
        await r.ping()
        await r.close()
        result["checks"]["redis"] = "ok"
    except Exception as e:
        result["checks"]["redis"] = f"error: {e}"

    # Meilisearch
    try:
        import meilisearch
        client = meilisearch.Client(settings.MEILI_HOST, settings.MEILI_KEY)
        client.health()
        result["checks"]["meilisearch"] = "ok"
    except Exception as e:
        result["checks"]["meilisearch"] = f"error: {e}"

    # Disk usage
    try:
        usage = shutil.disk_usage(settings.STORAGE_PATH)
        result["checks"]["disk"] = {
            "total_gb": round(usage.total / (1024**3), 1),
            "used_gb": round(usage.used / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
        }
    except Exception:
        result["checks"]["disk"] = "unavailable"

    return result
