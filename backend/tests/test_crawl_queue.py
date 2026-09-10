import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import MissingGreenlet

from app.services.crawl_runner import (
    _next_pending_task_ids,
    _worker_loop,
    run_crawl_task_async,
)
from app.core.database import get_db
from app.main import app
from app.services.auth import get_current_user
from app.services.sync import SyncPaused
from app.core.config import settings


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
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="admin",
        role="admin",
    )
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
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="admin",
        role="admin",
    )
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
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="admin",
        role="admin",
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/crawl/tasks/task-1/resume")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_next_pending_task_ids_returns_batch():
    db = AsyncMock()
    db.scalars = AsyncMock(
        return_value=SimpleNamespace(all=lambda: ["task-a", "task-b"])
    )
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.crawl_runner.SessionLocal", return_value=session):
        ids = await _next_pending_task_ids(3)

    assert ids == ["task-a", "task-b"]


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


@pytest.mark.asyncio
async def test_run_crawl_task_async_marks_paused_when_checkpoint_raises():
    task = _task()
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def discover_and_sync_all(self, *args, **kwargs):
            raise SyncPaused("paused")

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("app.services.crawl_runner.SessionLocal", return_value=session),
        patch("app.services.sync.SyncService", FakeSyncService),
    ):
        result = await run_crawl_task_async("task-1")

    assert result == {"status": "paused", "task_id": "task-1"}
    assert task.status == "paused"


def _session_for(db):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def test_is_transient_task_error_classification():
    from app.services.crawl_runner import _is_transient_task_error

    assert _is_transient_task_error(httpx.ConnectTimeout("")) is True
    assert _is_transient_task_error(httpx.ReadError("connection reset")) is True
    assert _is_transient_task_error(
        RuntimeError("书源目录暂时无法访问（网络/代理错误，请稍后重试）：ConnectTimeout")
    ) is True
    # WAF / rule problems must not be retried in a loop.
    assert _is_transient_task_error(
        RuntimeError("Site returned an anti-bot/captcha page: https://x/")
    ) is False
    assert _is_transient_task_error(
        RuntimeError("该书源的发现规则是 Legado JS 脚本（<js>/@js:），当前环境无法执行")
    ) is False
    assert _is_transient_task_error(
        RuntimeError("书源未返回可同步的书籍，请检查书源规则、Cookie 或站点验证状态。")
    ) is False


@pytest.mark.asyncio
async def test_run_crawl_task_async_retries_transient_network_failure():
    """A short proxy/network outage must not mark the whole task failed."""
    task = _task()
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def discover_and_sync_all(self, *args, **kwargs):
            raise httpx.ConnectTimeout("")

    with (
        patch("app.services.crawl_runner.SessionLocal", return_value=_session_for(db)),
        patch("app.services.sync.SyncService", FakeSyncService),
    ):
        result = await run_crawl_task_async("task-1")

    assert result["status"] == "retrying"
    assert task.status == "pending"
    assert task.resume_at is not None
    assert task.progress["auto_retries"] == 1
    assert "重试" in task.error


@pytest.mark.asyncio
async def test_run_crawl_task_async_does_not_retry_captcha_failure():
    task = _task()
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def discover_and_sync_all(self, *args, **kwargs):
            raise RuntimeError("Site returned an anti-bot/captcha page: https://x/")

    with (
        patch("app.services.crawl_runner.SessionLocal", return_value=_session_for(db)),
        patch("app.services.sync.SyncService", FakeSyncService),
    ):
        with pytest.raises(RuntimeError, match="anti-bot"):
            await run_crawl_task_async("task-1")

    assert task.status == "failed"


@pytest.mark.asyncio
async def test_worker_loop_picks_up_new_task_while_another_is_running():
    calls = 0
    started_tasks: set[str] = set()
    release_a = asyncio.Event()
    all_started = asyncio.Event()

    async def fake_next(limit: int) -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ["task-a"]
        if calls == 2:
            return []
        if calls == 3:
            return ["task-b"]
        return []

    async def fake_run(task_id: str) -> dict:
        started_tasks.add(task_id)
        if len(started_tasks) >= 2:
            all_started.set()
        if task_id == "task-a":
            await release_a.wait()
        return {"status": "completed", "task_id": task_id}

    with (
        patch.object(settings, "SYNC_WORKER_CONCURRENCY", 2),
        patch("app.services.crawl_runner.sync_thread_count", return_value=9),
        patch(
            "app.services.crawl_runner._next_pending_task_ids",
            side_effect=fake_next,
        ),
        patch(
            "app.services.crawl_runner.run_crawl_task_async",
            side_effect=fake_run,
        ),
    ):
        worker = asyncio.create_task(_worker_loop())
        try:
            await asyncio.wait_for(all_started.wait(), timeout=5)
        finally:
            release_a.set()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    assert started_tasks == {"task-a", "task-b"}
