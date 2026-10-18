"""API-level tests for the background search-index rebuild.

The rebuild reads every chapter file and pushes ~760 000 documents: hours of
work that used to sit inside the POST request, where a 30 s gateway timeout
turned it into ``499`` with the work still running and nothing to look at
(codex-handoff 60).  These tests pin the contract the admin UI polls:

    POST /api/search/index/rebuild          -> starts (or reports) the run
    GET  /api/search/index/rebuild/status   -> counters for the progress bar
    POST /api/search/index/rebuild/cancel   -> stop at the next batch
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.services.auth import get_current_user


def fake_user():
    return SimpleNamespace(
        id="u1", role="super_admin", r18_enabled=True, non_r18_enabled=True,
    )


def fake_db():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.scalars = AsyncMock(return_value=[])
    db.scalar = AsyncMock(return_value=0)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    return db


async def call(method, path, db, *, json=None):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: fake_user()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=json)
    finally:
        app.dependency_overrides.clear()


PROGRESS = {
    "running": True,
    "phase": "chapters",
    "books_total": 3,
    "books_done": 3,
    "chapters_total": 10,
    "chapters_done": 4,
    "documents_total": 13,
    "documents_done": 7,
    "percent": 53.8,
    "message": "正在写入章节 4 / 10",
    "error": None,
    "result": None,
    "cancel_requested": False,
}


@pytest.mark.asyncio
async def test_rebuild_returns_immediately_and_reports_running():
    with patch("app.api.routes.search.search_service.start_rebuild",
               return_value=dict(PROGRESS)):
        resp = await call("POST", "/api/search/index/rebuild", fake_db())

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["progress"]["percent"] == 53.8


@pytest.mark.asyncio
async def test_rebuild_status_serves_the_counters():
    with patch("app.api.routes.search.read_rebuild_progress",
               return_value=dict(PROGRESS)):
        resp = await call("GET", "/api/search/index/rebuild/status", fake_db())

    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["chapters_done"] == 4
    assert body["documents_total"] == 13


@pytest.mark.asyncio
async def test_rebuild_wait_true_keeps_the_blocking_behaviour():
    service = MagicMock()
    service.rebuild_index = AsyncMock(return_value={"books": 3, "chapters": 10})
    service.rebuild_progress_callback = MagicMock(return_value=lambda _update: None)

    with patch("app.api.routes.search.search_service", service):
        resp = await call("POST", "/api/search/index/rebuild?wait=true", fake_db())

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "books": 3, "chapters": 10}
    service.start_rebuild.assert_not_called()


@pytest.mark.asyncio
async def test_rebuild_cancel_is_passed_through():
    cancelled = dict(PROGRESS, cancel_requested=True, message="已请求取消，等当前批次写完就停…")
    service = MagicMock()
    service.cancel_rebuild = MagicMock(return_value=cancelled)

    with patch("app.api.routes.search.search_service", service):
        resp = await call("POST", "/api/search/index/rebuild/cancel", fake_db())

    assert resp.status_code == 200
    assert resp.json()["cancel_requested"] is True
