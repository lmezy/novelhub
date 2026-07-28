"""Tests for auth endpoints."""

import pytest


@pytest.mark.asyncio
async def test_login_invalid(client):
    resp = await client.post("/api/auth/login", json={
        "username": "nonexistent_user_xyz",
        "password": "wrong",
    })
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