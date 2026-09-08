import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.cookie_health import CookieHealthService


def _cookie(source: str = "src1", cookie_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(source=source, id=cookie_id, expired_at=None)


def _session(cookies) -> MagicMock:
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=cookies)
    db.rollback = AsyncMock()
    return db


class _ACM:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_check_all_cookies_counts_valid():
    session = _session([_cookie()])
    with (
        patch("app.services.cookie_health.SessionLocal", return_value=_ACM(session)),
        patch.object(
            CookieHealthService,
            "_validate_cookie",
            AsyncMock(return_value=True),
        ),
    ):
        result = await CookieHealthService.check_all_cookies()

    assert result["checked"] == 1
    assert result["valid"] == 1
    assert result["details"][0]["status"] == "valid"


@pytest.mark.asyncio
async def test_check_all_cookies_counts_invalid_without_refresh():
    session = _session([_cookie()])
    with (
        patch("app.services.cookie_health.SessionLocal", return_value=_ACM(session)),
        patch.object(
            CookieHealthService,
            "_validate_cookie",
            AsyncMock(return_value=False),
        ),
        patch.object(
            CookieHealthService,
            "_try_refresh",
            AsyncMock(return_value={"success": False, "error": "no creds"}),
        ),
    ):
        result = await CookieHealthService.check_all_cookies()

    assert result["expired"] == 1
    assert result["failed"] == 1
    assert result["details"][0]["reason"] == "invalid"


@pytest.mark.asyncio
async def test_check_all_cookies_times_out_slow_validation():
    session = _session([_cookie()])
    fake_settings = SimpleNamespace(COOKIE_CHECK_ITEM_TIMEOUT=1)

    async def slow_validate(cookie):
        await asyncio.sleep(5)
        return True

    with (
        patch("app.services.cookie_health.SessionLocal", return_value=_ACM(session)),
        patch("app.services.cookie_health.settings", fake_settings),
        patch.object(
            CookieHealthService,
            "_validate_cookie",
            slow_validate,
        ),
    ):
        result = await CookieHealthService.check_all_cookies()

    assert result["checked"] == 1
    assert result["failed"] == 1
    assert result["details"][0]["reason"] == "timeout"
    session.rollback.assert_awaited()
