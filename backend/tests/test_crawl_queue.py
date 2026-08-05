from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import MissingGreenlet

from app.services.crawl_runner import run_crawl_task_async
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


@pytest.mark.asyncio
async def test_crawl_runner_progress_does_not_lazy_load_expired_orm_state():
    class GuardedTask:
        def __init__(self):
            self.id = "task-1"
            self.source = "yuedu_a"
            self.max_pages = 200
            self.status = "pending"
            self.started_at = None
            self.finished_at = None
            self.error = None
            self.result = None
            self._progress = {"next_page": 1}
            self._locked = False

        @property
        def progress(self):
            if self._locked:
                raise MissingGreenlet(
                    "greenlet_spawn has not been called; can't call await_only() here"
                )
            return self._progress

        @progress.setter
        def progress(self, value):
            self._progress = value

    task = GuardedTask()
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    db.refresh = AsyncMock()
    commits = 0

    async def fake_commit():
        nonlocal commits
        commits += 1
        if commits == 2:
            # Simulate the session expiring attributes after a progress commit.
            task._locked = True

    db.commit = AsyncMock(side_effect=fake_commit)
    chapter_seen: dict = {}

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def discover_and_sync_all(self, *args, **kwargs):
            nonlocal chapter_seen
            await kwargs["progress_cb"](1, 1, 1, 0)
            await kwargs["chapter_progress_cb"]({
                "book_title": "Book",
                "chapter_title": "Chapter 1",
                "created_chapters": 1,
                "skipped_chapters": 0,
                "failed_chapters": 0,
                "total_chapters": 1,
            })
            chapter_seen = dict(task._progress)
            return {
                "pages_checked": 1,
                "books_found": 1,
                "books_synced": 1,
                "books_failed": 0,
                "chapters_created": 1,
                "chapters_skipped": 0,
                "chapters_failed": 0,
            }

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.crawl_runner.SessionLocal", return_value=session),
        patch("app.services.sync.SyncService", FakeSyncService),
    ):
        result = await run_crawl_task_async("task-1")

    assert result["books_synced"] == 1
    assert task.status == "completed"
    assert chapter_seen["current_book"] == "Book"
    assert chapter_seen["pages_checked"] == 1
