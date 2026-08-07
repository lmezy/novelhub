from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.api.routes.books import list_books
from app.services.auth import get_current_user, require_admin
from app.services.r18 import detect_r18
from app.services.visibility import (
    can_view_all_ages,
    can_view_r18,
    ensure_book_visible,
    visible_tags,
)


def test_detect_r18_source_flag_wins():
    assert detect_r18(source_is_r18=True, title="Normal") is True


def test_detect_r18_from_metadata():
    assert detect_r18(description="18禁 content") is True
    assert detect_r18(tags=["成人"]) is True
    assert detect_r18(title="A normal novel") is False


def test_can_view_r18():
    admin_off = SimpleNamespace(role="admin", r18_enabled=False)
    super_admin_on = SimpleNamespace(role="super_admin", r18_enabled=True)
    enabled_user = SimpleNamespace(role="user", r18_enabled=True)
    normal_user = SimpleNamespace(role="user", r18_enabled=False)

    assert can_view_r18(admin_off) is False
    assert can_view_r18(super_admin_on) is True
    assert can_view_r18(enabled_user) is True
    assert can_view_r18(normal_user) is False


def test_can_view_all_ages():
    admin_off = SimpleNamespace(role="admin", non_r18_enabled=False)
    super_admin_on = SimpleNamespace(role="super_admin", non_r18_enabled=True)
    enabled_user = SimpleNamespace(role="user", non_r18_enabled=True)
    disabled_user = SimpleNamespace(role="user", non_r18_enabled=False)

    assert can_view_all_ages(admin_off) is False
    assert can_view_all_ages(super_admin_on) is True
    assert can_view_all_ages(enabled_user) is True
    assert can_view_all_ages(disabled_user) is False


def test_book_visibility_matrix():
    r18_book = SimpleNamespace(is_r18=True, owner_id=None, is_public=False)
    normal_book = SimpleNamespace(is_r18=False, owner_id=None, is_public=False)
    off = SimpleNamespace(role="user", r18_enabled=False, non_r18_enabled=False)
    r18_only = SimpleNamespace(role="user", r18_enabled=True, non_r18_enabled=False)
    all_ages_only = SimpleNamespace(role="user", r18_enabled=False, non_r18_enabled=True)
    all = SimpleNamespace(role="user", r18_enabled=True, non_r18_enabled=True)
    admin_r18_only = SimpleNamespace(role="admin", r18_enabled=True, non_r18_enabled=False)
    admin_all_off = SimpleNamespace(role="super_admin", r18_enabled=False, non_r18_enabled=False)

    assert ensure_book_visible(off, r18_book) is False
    assert ensure_book_visible(off, normal_book) is False
    assert ensure_book_visible(r18_only, r18_book) is True
    assert ensure_book_visible(r18_only, normal_book) is False
    assert ensure_book_visible(all_ages_only, r18_book) is False
    assert ensure_book_visible(all_ages_only, normal_book) is True
    assert ensure_book_visible(all, r18_book) is True
    assert ensure_book_visible(all, normal_book) is True
    assert ensure_book_visible(admin_r18_only, r18_book) is True
    assert ensure_book_visible(admin_r18_only, normal_book) is False
    assert ensure_book_visible(admin_all_off, r18_book) is False
    assert ensure_book_visible(admin_all_off, normal_book) is False


def test_classification_tags_admin_only():
    tags = ["fantasy", "r18", "all-ages"]
    admin = SimpleNamespace(role="admin")
    user = SimpleNamespace(role="user")

    assert visible_tags(admin, tags) == tags
    assert visible_tags(user, tags) == ["fantasy"]


@pytest.mark.asyncio
async def test_admin_book_list_respects_visibility_switches():
    admin = SimpleNamespace(
        id="admin-1",
        role="super_admin",
        r18_enabled=False,
        non_r18_enabled=False,
    )
    db = AsyncMock()
    db.scalars.side_effect = [[], []]

    with patch("app.api.routes.books.list_book_custom_tags_map", new=AsyncMock(return_value={})):
        result = await list_books(admin, db)

    assert result == []
    query = db.scalars.call_args_list[0].args[0]
    assert len(query._where_criteria) > 0


@pytest.mark.asyncio
async def test_admin_toggle_user_r18():
    target = SimpleNamespace(
        id="user-1",
        username="reader",
        email="reader@example.com",
        role="user",
        r18_enabled=False,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=target)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        id="admin-1", role="admin"
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/admin/users/user-1/r18",
                json={"enabled": True},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert target.r18_enabled is True


@pytest.mark.asyncio
async def test_admin_update_user_visibility():
    target = SimpleNamespace(
        id="user-1",
        username="reader",
        email="reader@example.com",
        role="user",
        r18_enabled=True,
        non_r18_enabled=True,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=target)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        id="admin-1", role="admin"
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/admin/users/user-1/visibility",
                json={"non_r18_enabled": False},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert target.non_r18_enabled is False


@pytest.mark.asyncio
async def test_user_can_update_own_visibility_when_granted():
    user = SimpleNamespace(
        id="user-1",
        username="reader",
        email="reader@example.com",
        role="user",
        r18_enabled=False,
        non_r18_enabled=True,
        can_manage_visibility=True,
    )
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/auth/me/visibility",
                json={"r18_enabled": True, "non_r18_enabled": False},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert user.r18_enabled is True
    assert user.non_r18_enabled is False


@pytest.mark.asyncio
async def test_user_cannot_update_own_visibility_without_permission():
    user = SimpleNamespace(
        id="user-1",
        username="reader",
        email="reader@example.com",
        role="user",
        r18_enabled=False,
        non_r18_enabled=True,
        can_manage_visibility=False,
    )
    db = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/auth/me/visibility",
                json={"r18_enabled": True},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
