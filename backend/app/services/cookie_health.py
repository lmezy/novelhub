"""Cookie health check and auto-refresh service.

Periodically validates stored cookies and attempts automatic renewal
via auto_login when credentials are available.
"""

from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlalchemy import select

from app.core.database import SessionLocal
from app.crawler.registry import get_plugin
from app.models import Cookie
from app.models.source_credential import SourceCredential
from app.services.cookie_crypto import decrypt_cookie, encrypt_cookie


class CookieHealthService:
    """Checks cookie validity and refreshes expired/invalid cookies."""

    @staticmethod
    async def check_all_cookies() -> dict:
        """Check all stored cookies, refresh any that are expired or invalid."""
        results = {
            "checked": 0, "valid": 0, "expired": 0,
            "refreshed": 0, "failed": 0, "details": [],
        }
        async with SessionLocal() as db:
            cookies = await db.scalars(select(Cookie))
            cookie_list = list(cookies)
            results["checked"] = len(cookie_list)
            for cookie in cookie_list:
                detail = {"source": cookie.source, "cookie_id": cookie.id}
                if cookie.expired_at and cookie.expired_at < datetime.now(timezone.utc):
                    detail["reason"] = "expired"
                    results["expired"] += 1
                    refresh_result = await CookieHealthService._try_refresh(db, cookie)
                    detail.update(refresh_result)
                    key = "refreshed" if refresh_result.get("success") else "failed"
                    results[key] += 1
                else:
                    valid = await CookieHealthService._validate_cookie(cookie)
                    if valid:
                        results["valid"] += 1
                        detail["status"] = "valid"
                    else:
                        results["expired"] += 1
                        detail["reason"] = "invalid"
                        refresh_result = await CookieHealthService._try_refresh(db, cookie)
                        detail.update(refresh_result)
                        key = "refreshed" if refresh_result.get("success") else "failed"
                        results[key] += 1
                results["details"].append(detail)
        logger.info(
            "Cookie health: {} checked, {} valid, {} expired, {} refreshed, {} failed",
            results["checked"], results["valid"], results["expired"],
            results["refreshed"], results["failed"],
        )
        return results

    @staticmethod
    async def _validate_cookie(cookie: Cookie) -> bool:
        """Test whether a cookie is still valid via bookshelf fetch."""
        try:
            plugin = get_plugin(cookie.source)
            cookie_data = decrypt_cookie(cookie.cookie_data)
            plugin.set_cookie(cookie_data)
            shelf = await plugin.fetch_bookshelf(cookie_data)
            return shelf is not None
        except Exception:
            return False

    @staticmethod
    async def _try_refresh(db, cookie: Cookie) -> dict:
        """Attempt to refresh an expired cookie using stored credentials."""
        cred = await db.scalar(
            select(SourceCredential).where(SourceCredential.source == cookie.source)
        )
        if cred is None:
            return {"success": False, "error": "No credentials available for refresh"}
        try:
            plugin = get_plugin(cookie.source)
            new_cookie_str = await plugin.auto_login(cred.username, cred.password)
            if new_cookie_str:
                cookie.cookie_data = encrypt_cookie(new_cookie_str)
                cookie.expired_at = None
                await db.commit()
                logger.info("Cookie auto-refreshed for source {}", cookie.source)
                return {"success": True, "message": "Cookie auto-refreshed"}
            return {"success": False, "error": "Auto-login returned no cookie"}
        except Exception as exc:
            logger.error("Cookie refresh failed for {}: {}", cookie.source, exc)
            return {"success": False, "error": str(exc)}

    @staticmethod
    async def refresh_single_cookie(cookie_id: str) -> dict:
        """Manually refresh a single cookie by ID."""
        async with SessionLocal() as db:
            cookie = await db.get(Cookie, cookie_id)
            if cookie is None:
                raise ValueError(f"Cookie not found: {cookie_id}")
            result = await CookieHealthService._try_refresh(db, cookie)
            return result
