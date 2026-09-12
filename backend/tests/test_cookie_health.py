import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import MissingGreenlet

from app.services.cookie_health import CookieHealthService


def _cookie(source: str = "src1", cookie_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(
        source=source,
        id=cookie_id,
        cookie_data="encrypted",
        expired_at=None,
    )


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


@pytest.mark.asyncio
async def test_check_all_cookies_survives_rollback_expired_orm_state():
    """A timed-out check must not crash on expired ORM attributes.

    ``rollback()`` expires every instance in the session; reading ``source``
    afterwards raised MissingGreenlet and killed the whole 2am health check.
    """

    class ExpiringCookie:
        def __init__(self, source: str, cookie_id: str):
            self._source = source
            self.id = cookie_id
            self.cookie_data = "encrypted"
            self.expired_at = None
            self.expired = False

        @property
        def source(self):
            if self.expired:
                raise MissingGreenlet(
                    "greenlet_spawn has not been called; can't call await_only() here"
                )
            return self._source

    first = ExpiringCookie("src1", "c1")
    second = ExpiringCookie("src2", "c2")
    session = _session([first, second])

    async def expire_on_rollback():
        first.expired = True
        second.expired = True

    session.rollback = AsyncMock(side_effect=expire_on_rollback)

    async def slow_validate(cookie):
        await asyncio.sleep(5)
        return True

    fake_settings = SimpleNamespace(COOKIE_CHECK_ITEM_TIMEOUT=1)
    with (
        patch("app.services.cookie_health.SessionLocal", return_value=_ACM(session)),
        patch("app.services.cookie_health.settings", fake_settings),
        patch.object(CookieHealthService, "_validate_cookie", slow_validate),
    ):
        result = await CookieHealthService.check_all_cookies()

    assert result["checked"] == 2
    assert result["failed"] == 2
    assert [d["source"] for d in result["details"]] == ["src1", "src2"]
