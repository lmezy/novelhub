from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.cookies import create_cookie, delete_cookie, update_cookie
from app.schemas.cookie import CookieCreate, CookieUpdate


def _db_and_source():
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    source = SimpleNamespace(id="src-1", owner_id=None)
    db.get = AsyncMock(return_value=source)
    return db, source


@pytest.mark.asyncio
async def test_create_cookie_commits():
    db, _source = _db_and_source()
    cookie = SimpleNamespace(id="cookie-1", source="src-1")
    repo = MagicMock()
    repo.get_by_source = AsyncMock(return_value=None)
    repo.add = AsyncMock(return_value=cookie)

    with patch("app.api.routes.cookies.CookieRepository", return_value=repo):
        result = await create_cookie(
            CookieCreate(source="src-1", cookie_data="a=b"),
            SimpleNamespace(role="super_admin"),
            db,
        )

    assert result is cookie
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(cookie)


@pytest.mark.asyncio
async def test_update_cookie_commits():
    db, _source = _db_and_source()
    cookie = SimpleNamespace(id="cookie-1", source="src-1", cookie_data="old", expired_at=None)
    repo = MagicMock()
    repo.get = AsyncMock(return_value=cookie)

    with patch("app.api.routes.cookies.CookieRepository", return_value=repo):
        result = await update_cookie(
            "cookie-1",
            CookieUpdate(cookie_data="new"),
            SimpleNamespace(role="super_admin"),
            db,
        )

    assert result is cookie
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_cookie_commits():
    db, _source = _db_and_source()
    cookie = SimpleNamespace(id="cookie-1", source="src-1")
    repo = MagicMock()
    repo.get = AsyncMock(return_value=cookie)
    repo.delete = AsyncMock()

    with patch("app.api.routes.cookies.CookieRepository", return_value=repo):
        await delete_cookie(
            "cookie-1",
            SimpleNamespace(role="super_admin"),
            db,
        )

    repo.delete.assert_awaited_once_with(cookie)
    db.commit.assert_awaited_once()
