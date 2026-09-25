"""API tokens must actually authenticate something -- and nothing more.

Three separate defects used to sit behind the settings page's ``API 令牌``
panel, which promises "API 令牌用于第三方工具访问 NovelHub":

1. ``routes/tokens.py`` used ``ApiToken`` without importing it, so
   ``POST /api/tokens`` raised ``NameError`` and answered 500 -- a token could
   not even be created.
2. ``services/auth.py`` had ``get_current_user_or_token`` (the only code that
   reads ``X-API-Token``) with zero callers, so a token that existed was
   accepted by nothing.
3. Nothing pinned *which* routes accept a token, so the first person to reuse
   the dependency on a write or admin route would have silently turned a
   pasted-in third-party secret into full account access.

These tests cover all three: creation, acceptance on the read-only surface,
rejection of bad/revoked tokens, and the exact route inventory.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.api.routes import admin, books, chapters, search
from app.core.database import get_db
from app.main import app
from app.models import ApiToken, User
from app.services.auth import (
    get_current_user,
    get_current_user_or_token,
    require_admin,
)

TOKEN_PREFIX = "nh_abc12345"
RAW_TOKEN = "nh_raw-secret-value"


def _user() -> User:
    return User(id="u1", username="reader", role="user")


def _token_row(**overrides) -> ApiToken:
    values = {
        "id": "t1",
        "user_id": "u1",
        "name": "koreader",
        "token_hash": "deadbeef",
        "token_prefix": TOKEN_PREFIX,
        "is_active": True,
    }
    values.update(overrides)
    return ApiToken(**values)


# --------------------------------------------------------------------------
# 1. The token can be created at all
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_token_route_returns_the_plaintext_and_prefix():
    """``ApiToken`` is referenced in this module, so it must be imported here.

    Before the fix the handler died with ``NameError: name 'ApiToken' is not
    defined`` *after* the row was written, so the caller got a 500 and never
    saw the one-time plaintext token.
    """
    db = AsyncMock()
    db.get = AsyncMock(return_value=_token_row())

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _user()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/tokens", json={"name": "koreader"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["prefix"] == TOKEN_PREFIX
    # The plaintext is returned exactly once and is not the stored hash.
    assert body["token"] and body["token"] != "deadbeef"


# --------------------------------------------------------------------------
# 2. The dependency itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_api_token_resolves_to_its_owner():
    db = AsyncMock()
    db.get = AsyncMock(return_value=_user())

    with patch(
        "app.services.token_service.TokenService.verify_token",
        AsyncMock(return_value=_token_row()),
    ):
        user = await get_current_user_or_token(
            credentials=None, x_api_token=RAW_TOKEN, db=db
        )

    assert user.id == "u1"


@pytest.mark.asyncio
async def test_an_unknown_api_token_is_rejected():
    """A wrong token must 401 instead of falling through to the bearer branch."""
    db = AsyncMock()
    db.get = AsyncMock(return_value=_user())

    with patch(
        "app.services.token_service.TokenService.verify_token",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_or_token(
                credentials=None, x_api_token="nh_not-a-real-token", db=db
            )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid API token"


@pytest.mark.asyncio
async def test_a_revoked_token_no_longer_resolves():
    """``verify_token`` returns None for a revoked row; the header must 401."""
    db = AsyncMock()

    with patch(
        "app.services.token_service.TokenService.verify_token",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_or_token(
                credentials=None, x_api_token=RAW_TOKEN, db=db
            )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_a_token_header_wins_over_no_bearer_at_all():
    """Without either credential the dependency still refuses the request."""
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_token(
            credentials=None, x_api_token=None, db=db
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing authentication"


@pytest.mark.asyncio
async def test_the_bearer_branch_still_works_unchanged():
    """A JWT request must behave exactly as it did before the token branch."""
    db = AsyncMock()
    db.get = AsyncMock(return_value=_user())

    with patch("app.services.auth.decode_token", MagicMock(return_value={"sub": "u1"})):
        user = await get_current_user_or_token(
            credentials=MagicMock(credentials="jwt"),
            x_api_token=None,
            db=db,
        )

    assert user.id == "u1"


# --------------------------------------------------------------------------
# 3. The token surface may not grow by accident
# --------------------------------------------------------------------------

#: Every route that accepts ``X-API-Token``.  Read-only on purpose: a token
#: lives in a third-party client's config file, so it must not be able to
#: write, delete, or reach an admin route.  Adding an entry here is a
#: deliberate security decision, which is why this list is asserted exactly.
TOKEN_ACCEPTING_ROUTES = {
    "GET /api/books",
    "GET /api/books/{book_id}",
    "GET /api/books/{book_id}/chapters",
    "GET /api/chapters/{chapter_id}",
    "GET /api/chapters/{chapter_id}/content",
    "GET /api/chapters/{chapter_id}/content/meta",
    "GET /api/search",
    "POST /api/search/advanced",
}


def _dependency_calls(dependant) -> set:
    """Every callable FastAPI would resolve for one route, recursively."""
    found: set = set()
    stack = [dependant]
    while stack:
        current = stack.pop()
        if current.call is not None:
            found.add(current.call)
        stack.extend(current.dependencies)
    return found


def _module_routes(module_router) -> list[tuple[str, APIRoute]]:
    """``(full path, route)`` for one flat router, independent of FastAPI's
    internal route-tree representation (0.141 keeps included routers lazy, so
    walking ``app.routes`` no longer lists the included endpoints at all)."""
    prefix = getattr(module_router, "prefix", "") or ""
    pairs = []
    for route in module_router.routes:
        if not isinstance(route, APIRoute):
            continue
        path = route.path
        if prefix and not path.startswith(prefix):
            path = prefix + path
        pairs.append(("/api" + path, route))
    return pairs


def _routes_using(dependency) -> set[str]:
    used: set[str] = set()
    for module_router in (books.router, chapters.router, search.router):
        for path, route in _module_routes(module_router):
            if dependency not in _dependency_calls(route.dependant):
                continue
            for method in route.methods - {"HEAD", "OPTIONS"}:
                used.add(f"{method} {path}")
    return used


def test_the_token_surface_is_exactly_the_read_only_library():
    assert _routes_using(get_current_user_or_token) == TOKEN_ACCEPTING_ROUTES


def test_those_three_modules_are_the_only_ones_referencing_the_dependency():
    """The inventory above is only exhaustive while the import stays local.

    ``books``/``chapters``/``search`` are the whole read-only surface a
    third-party client needs.  A future route module importing the same
    dependency would silently escape the check, so the import itself is
    asserted here.
    """
    routes_dir = Path(books.__file__).parent
    referencing = sorted(
        path.name
        for path in routes_dir.glob("*.py")
        if "get_current_user_or_token" in path.read_text(encoding="utf-8")
    )
    assert referencing == ["books.py", "chapters.py", "search.py"]


def test_no_admin_route_accepts_an_api_token():
    """Admin routes resolve through ``get_current_user``/``require_admin``.

    A token header present without a bearer credential must therefore 401 --
    checked here on every admin route reachable from the flat routers.
    """
    checked = 0
    for module_router in (admin.router,):
        for path, route in _module_routes(module_router):
            calls = _dependency_calls(route.dependant)
            if require_admin not in calls and get_current_user not in calls:
                continue
            checked += 1
            assert get_current_user_or_token not in calls, path

    assert checked, "the admin surface disappeared; this guard is pointless"
