from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.invites import create_invite, delete_invite, list_invites
from app.api.routes.auth import register
from app.api.routes.admin import list_users
from app.schemas.user import UserCreate


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_create_invite_generates_one_time_code():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "settings", {}))
    user = SimpleNamespace(id="u1", role="user")

    with patch("app.api.routes.invites.generate_invite_code", return_value="ABC123"):
        invite = await create_invite(user, db)

    assert invite.code == "ABC123"
    assert invite.created_by == "u1"
    assert invite.expires_at > _utcnow()
    assert invite.expires_at <= _utcnow() + timedelta(days=1)


@pytest.mark.asyncio
async def test_list_invites_returns_own_invites():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.scalars = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [])
    )
    user = SimpleNamespace(id="u1", role="user")

    result = await list_invites(user, db)

    assert result == []


@pytest.mark.asyncio
async def test_delete_invite_checks_owner():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id="i1", created_by="u1"))
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    user = SimpleNamespace(id="u1", role="user")

    await delete_invite("i1", user, db)

    db.delete.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_invite_forbids_other_user():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id="i1", created_by="u2"))
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    user = SimpleNamespace(id="u1", role="user")

    with pytest.raises(HTTPException) as exc_info:
        await delete_invite("i1", user, db)

    assert exc_info.value.status_code == 404
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_consumes_one_time_invite():
    invite = SimpleNamespace(
        id="inv-1",
        code="ABC123",
        created_by="u1",
        expires_at=_utcnow() + timedelta(hours=12),
        used_by=None,
    )
    db = AsyncMock()
    scalar_calls = 0

    async def fake_scalar(*args, **kwargs):
        nonlocal scalar_calls
        scalar_calls += 1
        return invite if scalar_calls == 1 else None

    db.scalar = AsyncMock(side_effect=fake_scalar)
    db.get = AsyncMock(return_value=SimpleNamespace(id="u1", username="inviter"))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "settings", {}))

    result = await register(
        UserCreate(
            username="newuser",
            email="newuser@example.com",
            password="New_pass1",
            invite_code="ABC123",
        ),
        db,
    )

    assert invite.used_by is not None
    assert invite.used_at is not None
    created_user = db.add.call_args.args[0]
    assert created_user.invite_tag == "inviter"
    assert created_user.nickname
    assert result.status == "approved"


@pytest.mark.asyncio
async def test_list_users_hides_invite_tag_from_admin():
    db = AsyncMock()
    db.scalars = AsyncMock(
        return_value=[SimpleNamespace(invite_tag="inviter")]
    )

    result = await list_users(
        SimpleNamespace(id="admin", role="admin"),
        db,
    )

    assert result[0].invite_tag is None


@pytest.mark.asyncio
async def test_list_users_shows_invite_tag_to_super_admin():
    db = AsyncMock()
    db.scalars = AsyncMock(
        return_value=[SimpleNamespace(invite_tag="inviter")]
    )

    result = await list_users(
        SimpleNamespace(id="super", role="super_admin"),
        db,
    )

    assert result[0].invite_tag == "inviter"
