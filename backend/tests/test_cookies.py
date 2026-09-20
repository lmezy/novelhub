from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.cookies import create_cookie, delete_cookie, update_cookie
from app.models.cookie import Cookie
from app.schemas.cookie import CookieCreate, CookieUpdate
from app.services.cookie_crypto import decrypt_cookie


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
async def test_update_cookie_replaces_the_stored_value():
    """Saving a new Cookie must overwrite the old one, not append or ignore it."""
    db, _source = _db_and_source()
    cookie = SimpleNamespace(id="cookie-1", source="src-1", cookie_data="old", expired_at=None)
    repo = MagicMock()
    repo.get = AsyncMock(return_value=cookie)

    with patch("app.api.routes.cookies.CookieRepository", return_value=repo):
        await update_cookie(
            "cookie-1",
            CookieUpdate(cookie_data="ss_userid=42; cf_clearance=fresh"),
            SimpleNamespace(role="super_admin"),
            db,
        )

    assert cookie.cookie_data != "old"
    assert decrypt_cookie(cookie.cookie_data) == "ss_userid=42; cf_clearance=fresh"


def test_updating_a_stored_cookie_records_when_it_was_last_written():
    """``cookies.updated_at`` has to move on UPDATE, not only on INSERT.

    ``created_at`` is when the row first appeared, and the AI diagnosis rendered
    it as「Cookie 保存时间」— so a Cookie pasted that morning was reported as
    saved 40 days earlier and blamed for being expired.
    """
    engine = create_engine("sqlite://")
    Cookie.__table__.create(engine)

    with Session(engine) as session:
        session.add(Cookie(
            id="c1", source="yuedu_abc", cookie_data="old",
            created_at=datetime(2000, 1, 1), updated_at=datetime(2000, 1, 1),
        ))
        session.commit()

        session.get(Cookie, "c1").cookie_data = "new"
        session.commit()
        session.expire_all()
        row = session.get(Cookie, "c1")

        assert row.cookie_data == "new"
        assert row.created_at == datetime(2000, 1, 1)
        assert row.updated_at > datetime(2000, 1, 2)


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
