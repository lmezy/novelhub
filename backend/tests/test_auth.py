"""Tests for auth endpoints."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.database import get_db
from app.main import app
from app.schemas.auth import TokenOut
from app.schemas.user import UserOut


def test_token_out_accepts_user_attributes():
    user = SimpleNamespace(
        id="1",
        username="admin",
        email="admin@example.com",
        role="super_admin",
        r18_enabled=True,
        non_r18_enabled=True,
        can_manage_visibility=True,
    )
    out = TokenOut(access_token="token", user=user)
    assert out.user.username == "admin"
    assert UserOut.model_validate(user).role == "super_admin"


@pytest.mark.asyncio
async def test_login_invalid(client):
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        resp = await client.post("/api/auth/login", json={
            "username": "nonexistent_user_xyz",
            "password": "wrong",
        })
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_validation(client):
    """Register without password should fail."""
    resp = await client.post("/api/auth/register", json={
        "username": "test",
        "email": "test@test.com",
    })
    assert resp.status_code in (422, 500)


@pytest.mark.asyncio
async def test_api_test_endpoint(client):
    resp = await client.get("/api/test")
    assert resp.status_code == 200
    assert resp.json()["message"] == "NovelHub API"


@pytest.mark.asyncio
async def test_me_requires_auth(client):
    """GET /api/auth/me without token should fail."""
    resp = await client.get("/api/auth/me")
    assert resp.status_code in (401, 403, 500)
