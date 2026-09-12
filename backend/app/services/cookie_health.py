"""Cookie health check and auto-refresh service.

Periodically validates stored cookies and attempts automatic renewal
via auto_login when credentials are available.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlalchemy import select

from app.core.database import SessionLocal
from app.crawler.registry import get_plugin
from app.models import Cookie
from app.models.source_credential import SourceCredential
from app.services.cookie_crypto import safe_decrypt_cookie, encrypt_cookie
from app.core.config import settings


class CookieHealthService:
    """Checks cookie validity and refreshes expired/invalid cookies."""

    @staticmethod
    def _snapshot_field(cookie, name: str):
        """Read a cookie field from a snapshot dict or a live ORM object."""
        if isinstance(cookie, dict):
            return cookie.get(name)
        return getattr(cookie, name)

    @staticmethod
    async def check_all_cookies() -> dict:
        """Check all stored cookies, refresh any that are expired or invalid."""
        results = {
            "checked": 0, "valid": 0, "expired": 0,
            "refreshed": 0, "failed": 0, "details": [],
        }
        async with SessionLocal() as db:
            cookies = await db.scalars(select(Cookie))
            # Snapshot the identifiers up front: ``rollback()`` below expires
            # every ORM instance in this session, and reading an expired
            # attribute outside the greenlet context raises MissingGreenlet
            # (that crash aborted the whole 2am health check).
            cookie_list = [
                {
                    "obj": cookie,
                    "id": cookie.id,
                    "source": cookie.source,
                    "cookie_data": cookie.cookie_data,
                    "expired_at": cookie.expired_at,
                }
                for cookie in cookies
            ]
            results["checked"] = len(cookie_list)

            # A captcha/anti-bot source can make each validation take minutes
            # (proxy connect timeouts + bookshelf probing + Playwright).  Bound
            # per-item work so the 2am task cannot hammer a blocked source for
            # hours, which is what the user observes as "持续访问书源网站".
            item_timeout = max(
                1,
                int(getattr(settings, "COOKIE_CHECK_ITEM_TIMEOUT", 60)),
            )

            async def _check_one(cookie) -> dict:
                detail = {"source": cookie["source"], "cookie_id": cookie["id"]}
                expired_at = cookie["expired_at"]
                if expired_at and expired_at < datetime.now(timezone.utc):
                    detail["reason"] = "expired"
                    refresh_result = await CookieHealthService._try_refresh(db, cookie)
                    detail.update(refresh_result)
                    detail["_outcome"] = (
                        "refreshed" if refresh_result.get("success") else "failed"
                    )
                else:
                    valid = await CookieHealthService._validate_cookie(cookie)
                    if valid:
                        detail["status"] = "valid"
                        detail["_outcome"] = "valid"
                    else:
                        detail["reason"] = "invalid"
                        refresh_result = await CookieHealthService._try_refresh(db, cookie)
                        detail.update(refresh_result)
                        detail["_outcome"] = (
                            "refreshed" if refresh_result.get("success") else "failed"
                        )
                return detail

            for cookie in cookie_list:
                try:
                    detail = await asyncio.wait_for(_check_one(cookie), timeout=item_timeout)
                except asyncio.TimeoutError:
                    await db.rollback()
                    results["failed"] += 1
                    results["details"].append({
                        "source": cookie["source"],
                        "cookie_id": cookie["id"],
                        "reason": "timeout",
                        "status": False,
                        "error": f"cookie validation timed out after {item_timeout}s",
                    })
                    continue
                outcome = detail.pop("_outcome", "failed")
                # valid / refreshed / failed each increment their own bucket;
                # an expired-or-invalid cookie also counts toward "expired".
                results[outcome] += 1
                if detail.get("reason") in ("expired", "invalid"):
                    results["expired"] += 1
                results["details"].append(detail)
        logger.info(
            "Cookie health: {} checked, {} valid, {} expired, {} refreshed, {} failed",
            results["checked"], results["valid"], results["expired"],
            results["refreshed"], results["failed"],
        )
        return results

    @staticmethod
    async def _validate_cookie(cookie) -> bool:
        """Test whether a cookie is still valid via bookshelf fetch."""
        try:
            source = CookieHealthService._snapshot_field(cookie, "source")
            plugin = get_plugin(source)
            cookie_data = safe_decrypt_cookie(
                CookieHealthService._snapshot_field(cookie, "cookie_data")
            )
            plugin.set_cookie(cookie_data)
            shelf = await plugin.fetch_bookshelf(cookie_data)
            return shelf is not None
        except Exception:
            return False

    @staticmethod
    async def _try_refresh(db, cookie, source: str | None = None) -> dict:
        """Attempt to refresh an expired cookie using stored credentials."""
        source = source or CookieHealthService._snapshot_field(cookie, "source")
        record = CookieHealthService._snapshot_field(cookie, "obj") or cookie
        cred = await db.scalar(
            select(SourceCredential).where(SourceCredential.source == source)
        )
        if cred is None:
            return {"success": False, "error": "No credentials available for refresh"}
        try:
            plugin = get_plugin(source)
            new_cookie_str = await plugin.auto_login(cred.username, cred.password)
            if new_cookie_str:
                record.cookie_data = encrypt_cookie(new_cookie_str)
                record.expired_at = None
                await db.commit()
                logger.info("Cookie auto-refreshed for source {}", source)
                return {"success": True, "message": "Cookie auto-refreshed"}
            return {"success": False, "error": "Auto-login returned no cookie"}
        except Exception as exc:
            logger.error("Cookie refresh failed for {}: {}", source, exc)
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
