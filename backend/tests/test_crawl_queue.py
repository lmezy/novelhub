from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.services.auth import require_admin


def _task(**overrides):
    base = {
        "id": "task-1",
        "source": "yuedu_a",
        "mode": "discover_all",
        "max_pages": 200,
        "priority": 0,
        "status": "pending",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
        "progress": None,
        "created_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_pause_pending_task():
    task = _task()
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    db.commit = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/crawl/tasks/task-1/pause")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_move_task_front_sets_priority():
    task = _task()
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    db.scalar = AsyncMock(return_value=7)
    db.commit = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/crawl/tasks/task-1/move-front")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["priority"] == 8
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_resume_paused_task_requeues_it():
    task = _task(status="paused", started_at=None)
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    db.commit = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/crawl/tasks/task-1/resume")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
