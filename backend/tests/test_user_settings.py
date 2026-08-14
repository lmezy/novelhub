from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes.auth import (
    change_my_password,
    update_my_email,
    update_my_nickname,
    update_my_settings,
)
from app.schemas.user import (
    ChangePasswordRequest,
    UpdateEmailRequest,
    UpdateNicknameRequest,
    UserSettingsUpdate,
)
from fastapi import HTTPException
from pydantic import ValidationError
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
async def test_update_my_settings_saves_image_display_flags():
    user = SimpleNamespace(id="u1", settings={})
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    result = await update_my_settings(
        UserSettingsUpdate(show_covers=False, show_content_images=False),
        user,
        db,
    )

    assert result is user
    assert user.settings == {"show_covers": False, "show_content_images": False}


@pytest.mark.asyncio
async def test_update_my_settings_saves_tap_actions():
    user = SimpleNamespace(id="u1", settings={})
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    result = await update_my_settings(
        UserSettingsUpdate(tap_actions={"tl": "next_page", "mc": "prev_page"}),
        user,
        db,
    )

    assert result is user
    assert user.settings["tap_actions"] == {
        "tl": "next_page",
        "tc": "prev_page",
        "tr": "next_page",
        "ml": "prev_page",
        "mc": "menu",
        "mr": "next_page",
        "bl": "prev_page",
        "bc": "next_page",
        "br": "next_page",
    }
    assert db.commit.await_count == 1


def test_tap_actions_rejects_invalid_values():
    with pytest.raises(ValidationError):
        UserSettingsUpdate(tap_actions={"tl": "jump"})
    with pytest.raises(ValidationError):
        UserSettingsUpdate(tap_actions={"tc": 123})


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


@pytest.mark.asyncio
async def test_update_my_nickname_saves():
    user = SimpleNamespace(id="u1", nickname=None)
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    result = await update_my_nickname(
        UpdateNicknameRequest(nickname="新昵称"),
        user,
        db,
    )

    assert result is user
    assert user.nickname == "新昵称"


@pytest.mark.asyncio
async def test_update_my_nickname_rejects_null_and_empty():
    db = AsyncMock()

    for value in ("null", "  ", ""):
        with pytest.raises(HTTPException):
            await update_my_nickname(
                UpdateNicknameRequest(nickname=value),
                SimpleNamespace(id="u1", nickname="old"),
                db,
            )
