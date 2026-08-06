from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.services.auth import get_current_user, require_admin
from app.services.security import hash_password, verify_password
from app.services.account import username_available


def _db_with_scalar(value=None):
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=value)
    return db


def _db_with_scalar_sequence(values):
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=values)
    return db


@pytest.mark.asyncio
async def test_register_pending_when_approval_enabled():
    db = _db_with_scalar(None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "app.api.routes.auth.get_registration_approval_enabled",
                new=AsyncMock(return_value=True),
            ):
                resp = await client.post(
                    "/api/auth/register",
                    json={
                        "username": "pendinguser",
                        "email": "pending@example.com",
                        "password": "secret1",
                    },
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["access_token"] is None
    assert body["user"]["approved"] is False


@pytest.mark.asyncio
async def test_register_approved_when_approval_disabled():
    db = _db_with_scalar(None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "app.api.routes.auth.get_registration_approval_enabled",
                new=AsyncMock(return_value=False),
            ):
                resp = await client.post(
                    "/api/auth/register",
                    json={
                        "username": "approveduser",
                        "email": "approved@example.com",
                        "password": "secret1",
                    },
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "approved"
    assert body["access_token"]
    assert body["user"]["approved"] is True


@pytest.mark.asyncio
async def test_register_without_email_succeeds():
    db = _db_with_scalar(None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "app.api.routes.auth.get_registration_approval_enabled",
                new=AsyncMock(return_value=False),
            ):
                resp = await client.post(
                    "/api/auth/register",
                    json={"username": "noemail", "password": "secret1"},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["user"]["email"] is None


@pytest.mark.asyncio
async def test_register_rejects_invalid_username():
    db = _db_with_scalar(None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/register",
                json={"username": "中文用户", "password": "secret1"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_rejects_weak_password():
    db = _db_with_scalar(None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/register",
                json={"username": "weakpass", "password": "secret"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pending_user_cannot_login():
    user = SimpleNamespace(
        id="user-1",
        username="pendinguser",
        role="user",
        approved=False,
        password_hash=hash_password("secret1"),
    )
    db = _db_with_scalar(user)
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/login",
                json={"username": "pendinguser", "password": "secret1"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_login_by_email():
    user = SimpleNamespace(
        id="user-1",
        username="reader",
        role="user",
        approved=True,
        password_hash=hash_password("secret1"),
    )
    db = _db_with_scalar(user)
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/login",
                json={"username": "reader@example.com", "password": "secret1"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["access_token"]


@pytest.mark.asyncio
async def test_register_duplicate_username_rejected():
    db = _db_with_scalar_sequence([object()])
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/register",
                json={"username": "taken", "password": "secret1"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_duplicate_email_rejected():
    db = _db_with_scalar_sequence([None, object()])
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/register",
                json={
                    "username": "newuser",
                    "email": "taken@example.com",
                    "password": "secret1",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_username_reserved_by_recent_deleted_account():
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[None, object()])

    error = await username_available(db, "taken")

    assert error is not None


@pytest.mark.asyncio
async def test_admin_created_user_is_approved():
    db = _db_with_scalar(None)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        id="admin-1",
        role="admin",
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/admin/users",
                json={
                    "username": "createduser",
                    "email": "created@example.com",
                    "password": "secret1",
                    "role": "user",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["approved"] is True


@pytest.mark.asyncio
async def test_normal_admin_cannot_create_admin_user():
    db = _db_with_scalar(None)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        id="admin-1",
        role="admin",
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/admin/users",
                json={
                    "username": "newadmin",
                    "email": "newadmin@example.com",
                    "password": "secret1",
                    "role": "admin",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_can_change_user_password():
    target = SimpleNamespace(
        id="user-1",
        username="reader",
        email="reader@example.com",
        role="user",
        r18_enabled=False,
        non_r18_enabled=True,
        can_manage_visibility=False,
        approved=True,
        password_hash=hash_password("old-secret1"),
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=target)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="super-1",
        role="super_admin",
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/admin/users/user-1/password",
                json={"password": "new-secret1"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert target.password_hash != hash_password("old-secret1")
    assert verify_password("new-secret1", target.password_hash) is True


@pytest.mark.asyncio
async def test_normal_admin_cannot_change_user_password():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="admin-1",
        role="admin",
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/admin/users/user-1/password",
                json={"password": "new-secret1"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
