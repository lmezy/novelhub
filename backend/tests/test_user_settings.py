from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes.auth import (
    change_my_password,
    update_my_email,
    update_my_settings,
)
from app.schemas.user import ChangePasswordRequest, UpdateEmailRequest, UserSettingsUpdate
from app.services.security import hash_password, verify_password


@pytest.mark.asyncio
async def test_update_my_settings_saves_personal_preferences():
    user = SimpleNamespace(id="u1", settings={})
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    result = await update_my_settings(
        UserSettingsUpdate(font="serif", font_size=18, theme="dark"),
        user,
        db,
    )

    assert result is user
    assert user.settings == {"font": "serif", "font_size": 18, "theme": "dark"}
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_change_my_password_updates_hash():
    user = SimpleNamespace(
        id="u1",
        password_hash=hash_password("oldpass"),
    )
    db = AsyncMock()
    db.commit = AsyncMock()

    await change_my_password(
        ChangePasswordRequest(
            current_password="oldpass",
            new_password="New_pass1",
        ),
        user,
        db,
    )

    assert verify_password("New_pass1", user.password_hash)


@pytest.mark.asyncio
async def test_update_my_email_binds_email():
    user = SimpleNamespace(id="u1", email=None)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    result = await update_my_email(
        UpdateEmailRequest(email="user@example.com"),
        user,
        db,
    )

    assert result is user
    assert user.email == "user@example.com"
