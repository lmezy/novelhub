"""Chapter images are embedded as ``<img>`` tags, so they need cookie auth.

Reader HTML is served by an authenticated API call, but the images inside it
are fetched by the browser itself without the ``Authorization`` header, so a
bearer-only route answered 401 for every image of a synced chapter.
"""

import inspect

import pytest
from fastapi import Response
from starlette.requests import Request
from unittest.mock import AsyncMock, MagicMock

from app.api.routes.auth import _bearer_token, _set_media_cookie
from app.main import app
from app.models import User
from app.services.auth import (
    MEDIA_COOKIE_NAME,
    MEDIA_COOKIE_PATH,
    get_current_user_media,
)
from app.services.jwt import create_token


def _request(headers: dict[str, str] | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/chapters/c1/images/a.jpg",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
        "query_string": b"",
    })


@pytest.mark.asyncio
async def test_media_auth_accepts_the_login_cookie():
    token = create_token("user-1")
    db = AsyncMock()
    db.get = AsyncMock(return_value=MagicMock(id="user-1"))

    user = await get_current_user_media(
        request=_request({"cookie": f"{MEDIA_COOKIE_NAME}={token}"}),
        credentials=None,
        db=db,
    )

    assert user.id == "user-1"


@pytest.mark.asyncio
async def test_media_auth_still_accepts_a_bearer_token():
    token = create_token("user-2")
    db = AsyncMock()
    db.get = AsyncMock(return_value=MagicMock(id="user-2"))
    credentials = MagicMock(credentials=token)

    user = await get_current_user_media(
        request=_request(),
        credentials=credentials,
        db=db,
    )

    assert user.id == "user-2"


@pytest.mark.asyncio
async def test_media_auth_rejects_missing_and_invalid_credentials():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(Exception) as missing:
        await get_current_user_media(request=_request(), credentials=None, db=db)
    assert getattr(missing.value, "status_code", None) == 401

    with pytest.raises(Exception) as invalid:
        await get_current_user_media(
            request=_request({"cookie": f"{MEDIA_COOKIE_NAME}=not-a-jwt"}),
            credentials=None,
            db=db,
        )
    assert getattr(invalid.value, "status_code", None) == 401


def test_media_cookie_is_scoped_to_chapter_routes():
    response = Response()

    _set_media_cookie(response, "token-value")

    header = response.headers["set-cookie"]
    assert f"{MEDIA_COOKIE_NAME}=token-value" in header
    assert f"Path={MEDIA_COOKIE_PATH}" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header


def test_set_media_cookie_ignores_an_empty_token():
    response = Response()

    _set_media_cookie(response, "")

    assert "set-cookie" not in response.headers


def test_bearer_token_reads_the_authorization_header():
    assert _bearer_token(_request({"authorization": "Bearer abc.def"})) == "abc.def"
    assert _bearer_token(_request()) == ""
    assert _bearer_token(_request({"authorization": "Basic abc"})) == ""


def test_chapter_image_route_uses_the_media_dependency():
    from app.api.routes.chapters import get_chapter_image

    dependency = inspect.signature(get_chapter_image).parameters["user"].default

    assert dependency.dependency is get_current_user_media


def test_user_model_keeps_r18_flag_for_visibility_checks():
    # The media route reuses the normal visibility rules, so the dependency
    # must hand back a real user object rather than a bare id.
    assert hasattr(User, "r18_enabled")
