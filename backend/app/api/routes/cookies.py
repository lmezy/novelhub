from uuid import uuid4

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Cookie, User
from app.services.cookie_crypto import encrypt_cookie, decrypt_cookie
from app.repositories.cookie import CookieRepository
from app.schemas.cookie import CookieCreate, CookieOut, CookieUpdate
from app.services.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cookies",
    tags=["cookies"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=list[CookieOut])
async def list_cookies(db: AsyncSession = Depends(get_db)):
    repo = CookieRepository(db)
    return await repo.list()


@router.post("", response_model=CookieOut, status_code=201)
async def create_cookie(payload: CookieCreate, db: AsyncSession = Depends(get_db)):
    try:
        repo = CookieRepository(db)
        existing = await repo.get_by_source(payload.source)
        if existing:
            raise HTTPException(status_code=409, detail="Cookie for this source already exists")

        encrypted = encrypt_cookie(payload.cookie_data)
        cookie = Cookie(
            id=str(uuid4()),
            source=payload.source,
            cookie_data=encrypted,
            expired_at=payload.expired_at,
        )
        result = await repo.add(cookie)
        logger.info("Cookie saved for source=%s id=%s", payload.source, cookie.id)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to save cookie for source=%s", payload.source)
        raise HTTPException(status_code=500, detail=f"Internal error while saving cookie: {str(exc)[:300]}")


@router.get("/{cookie_id}", response_model=CookieOut)
async def get_cookie(cookie_id: str, db: AsyncSession = Depends(get_db)):
    repo = CookieRepository(db)
    cookie = await repo.get(cookie_id)
    if cookie is None:
        raise HTTPException(status_code=404, detail="Cookie not found")
    return cookie


@router.put("/{cookie_id}", response_model=CookieOut)
async def update_cookie(cookie_id: str, payload: CookieUpdate, db: AsyncSession = Depends(get_db)):
    repo = CookieRepository(db)
    cookie = await repo.get(cookie_id)
    if cookie is None:
        raise HTTPException(status_code=404, detail="Cookie not found")
    data = payload.model_dump(exclude_unset=True)
    if "cookie_data" in data and data["cookie_data"] is not None:
        cookie.cookie_data = encrypt_cookie(data["cookie_data"])
    if "expired_at" in data:
        cookie.expired_at = data["expired_at"]
    await db.flush()
    return cookie


@router.delete("/{cookie_id}", status_code=204)
async def delete_cookie(cookie_id: str, db: AsyncSession = Depends(get_db)):
    repo = CookieRepository(db)
    cookie = await repo.get(cookie_id)
    if cookie is None:
        raise HTTPException(status_code=404, detail="Cookie not found")
    await repo.delete(cookie)


@router.post("/test", status_code=200)
async def test_cookie(payload: CookieCreate, db: AsyncSession = Depends(get_db)):
    """Test if a cookie works by attempting to fetch the bookshelf.

    Does NOT save the cookie. Returns book count if successful.
    """
    from app.crawler.registry import get_plugin
    from app.models import Source

    # Look up source by ID to get its plugin_name and config
    source = await db.get(Source, payload.source)
    if source is None:
        raise HTTPException(status_code=400, detail=f"Source not found: {payload.source}")

    config = source.config if source.plugin_name == "yuedu" else None
    try:
        plugin = get_plugin(source.plugin_name, config=config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not hasattr(plugin, "fetch_bookshelf"):
        raise HTTPException(
            status_code=400,
            detail=f"Source '{payload.source}' does not support bookshelf fetch",
        )

    plugin.set_cookie(payload.cookie_data)

    try:
        shelf = await plugin.fetch_bookshelf(payload.cookie_data)
        return {
            "status": "ok",
            "message": f"Cookie is valid. Found {len(shelf)} books on bookshelf.",
            "books_count": len(shelf),
            "sample_books": [
                {"title": b.title, "author": b.author} for b in shelf[:5]
            ],
        }
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cookie test failed: {str(exc)[:300]}",
        ) from exc
    finally:
        if hasattr(plugin, "close"):
            try:
                await plugin.close()
            except Exception:
                pass


@router.post("/{cookie_id}/refresh", status_code=200)
async def refresh_cookie(cookie_id: str, db: AsyncSession = Depends(get_db)):
    """Refresh an expired cookie using stored credentials (auto-login)."""
    from app.services.cookie_health import CookieHealthService
    try:
        result = await CookieHealthService.refresh_single_cookie(cookie_id)
        if result.get("success"):
            return {"status": "ok", "message": result.get("message", "Cookie refreshed")}
        raise HTTPException(status_code=400, detail=result.get("error", "Refresh failed"))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/health-check", status_code=200)
async def check_all_cookies_health(db: AsyncSession = Depends(get_db)):
    """Check all cookies and auto-refresh any that are expired/invalid."""
    from app.services.cookie_health import CookieHealthService
    return await CookieHealthService.check_all_cookies()
