import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import MissingGreenlet

from app.services.crawl_runner import (
    _next_pending_tasks,
    _worker_loop,
    run_crawl_task_async,
)
from app.core.database import get_db
from app.main import app
from app.services.auth import get_current_user
from app.services.sync import SyncPaused
from app.core.config import settings


@pytest.mark.asyncio
async def test_recent_tasks_put_running_first_then_failed():
    """The sync page shows what is running now, then what broke."""
    from app.repositories.crawl_task import CrawlTaskRepository

    captured: dict[str, object] = {}

    class _EmptyResult:
        def __iter__(self):
            return iter(())

    db = AsyncMock()

    async def fake_scalars(query):
        captured["query"] = query
        return _EmptyResult()

    db.scalars = fake_scalars

    await CrawlTaskRepository(db).list_recent(limit=5)

    compiled = captured["query"].compile()  # type: ignore[union-attr]
    sql = str(compiled)
    params = set(compiled.params.values())

    assert {"running", "failed"} <= params
    assert "CASE" in sql
    assert sql.index("CASE") < sql.index("crawl_tasks.created_at DESC")


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
async def test_next_pending_tasks_returns_batch():
    rows = SimpleNamespace(
        all=lambda: [("task-a", "yuedu_a"), ("task-b", "yuedu_b")]
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=rows)
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.crawl_runner.SessionLocal", return_value=session):
        pairs = await _next_pending_tasks(3)

    assert pairs == [("task-a", "yuedu_a"), ("task-b", "yuedu_b")]


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
    # A retry always gets a result snapshot to merge later attempts into.
    assert task.result["books_found"] == 0


@pytest.mark.asyncio
async def test_run_crawl_task_async_carries_counters_across_retries():
    """A retried task must not lose (or double count) earlier progress."""
    task = _task(
        progress={
            "books_found": 78,
            "books_synced": 68,
            "books_failed": 10,
            "pages_checked": 1,
            "next_page": 1,
            "auto_retries": 0,
        }
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    calls = {"n": 0}

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def discover_and_sync_all(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # The previous attempt reported 78/68/10; this one finds
                # nothing more before the proxy drops the connection.
                raise httpx.ConnectTimeout("")
            await kwargs["progress_cb"](1, 5, 3, 2)
            raise httpx.ConnectTimeout("")

    with (
        patch("app.services.crawl_runner.SessionLocal", return_value=_session_for(db)),
        patch("app.services.sync.SyncService", FakeSyncService),
    ):
        first = await run_crawl_task_async("task-1")
        second = await run_crawl_task_async("task-1")

    assert first["status"] == "retrying"
    assert second["status"] == "retrying"
    assert task.result["books_found"] == 83
    assert task.result["books_synced"] == 71
    assert task.result["books_failed"] == 12
    assert task.progress["auto_retries"] == 2


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

    async def fake_next(limit: int, exclude_sources=None) -> list[tuple[str, str]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [("task-a", "yuedu_a")]
        if calls == 2:
            return []
        if calls == 3:
            return [("task-b", "yuedu_b")]
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
        patch(
            "app.services.crawl_runner._next_pending_tasks",
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


@pytest.mark.asyncio
async def test_worker_loop_runs_one_worker_per_source_when_uncapped():
    """``SYNC_WORKER_CONCURRENCY=0`` must not queue sources behind three slots.

    Online a fourth full-site task waited ~4h behind three long h528 / 要撸 /
    禁忌书屋 tasks, because the pool had exactly ``SYNC_WORKER_CONCURRENCY``
    (3) slots.  Every source now gets its own worker while each source still
    keeps its own request pacing.
    """
    tasks = [("task-1", "src-1"), ("task-2", "src-2"), ("task-3", "src-3"),
             ("task-4", "src-4")]
    started: list[str] = []
    all_started = asyncio.Event()
    release = asyncio.Event()
    served = False

    async def fake_next(limit, exclude_sources=None):
        nonlocal served
        if served:
            return []
        served = True
        return list(tasks)

    async def fake_run(task_id: str) -> dict:
        started.append(task_id)
        if len(started) >= len(tasks):
            all_started.set()
        await release.wait()
        return {"status": "completed", "task_id": task_id}

    with (
        patch.object(settings, "SYNC_WORKER_CONCURRENCY", 0),
        patch(
            "app.services.crawl_runner._next_pending_tasks",
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
            release.set()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    assert sorted(started) == ["task-1", "task-2", "task-3", "task-4"]


@pytest.mark.asyncio
async def test_worker_loop_never_runs_two_tasks_of_the_same_source():
    """Two queued tasks for one source must not hit that site at once."""
    pending = [("task-1", "src-1"), ("task-2", "src-1")]
    concurrency_seen: list[str] = []
    overlapping = False
    release = asyncio.Event()
    started = asyncio.Event()

    async def fake_next(limit, exclude_sources=None):
        if exclude_sources and "src-1" in exclude_sources:
            return []
        return list(pending)

    async def fake_run(task_id: str) -> dict:
        nonlocal overlapping
        if concurrency_seen:
            overlapping = True
        concurrency_seen.append(task_id)
        started.set()
        await release.wait()
        concurrency_seen.remove(task_id)
        return {"status": "completed", "task_id": task_id}

    with (
        patch.object(settings, "SYNC_WORKER_CONCURRENCY", 0),
        patch(
            "app.services.crawl_runner._next_pending_tasks",
            side_effect=fake_next,
        ),
        patch(
            "app.services.crawl_runner.run_crawl_task_async",
            side_effect=fake_run,
        ),
    ):
        worker = asyncio.create_task(_worker_loop())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            await asyncio.sleep(0.5)
        finally:
            release.set()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    assert overlapping is False
    assert concurrency_seen == []


@pytest.mark.asyncio
async def test_run_crawl_task_async_survives_expired_orm_state_on_failure():
    """A failed task must never stay ``running`` behind a MissingGreenlet.

    SyncService rolls the worker session back when a book fails, which expires
    every ORM instance.  The old error handler then read ``task_obj.progress``
    in non-async code, raised ``MissingGreenlet`` and left the row ``running``
    with no explanation (seen online as "Crawl task ... stopped: greenlet_spawn
    has not been called").
    """
    task = _task()
    task.progress = {"next_page": 1, "books_failed": 9}
    state = {"expired": False}

    # Make every ORM attribute read raise, exactly like an expired instance.
    class Guarded:
        def __init__(self, inner):
            self.__dict__["_inner"] = inner

        def __getattr__(self, name):
            if name == "progress" and state["expired"]:
                raise MissingGreenlet(
                    "greenlet_spawn has not been called; can't call await_only() here"
                )
            return getattr(self.__dict__["_inner"], name)

        def __setattr__(self, name, value):
            setattr(self.__dict__["_inner"], name, value)

    guarded = Guarded(task)

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def discover_and_sync_all(self, *args, **kwargs):
            # A failed book sync rolls the session back, which expires every
            # ORM instance the worker still holds.
            state["expired"] = True
            raise RuntimeError("同步连续失败超过 10 本，已中止任务。")

    async def fake_commit():
        if state["expired"]:
            raise MissingGreenlet("expired session")

    db = AsyncMock()
    db.get = AsyncMock(return_value=guarded)
    db.commit = AsyncMock(side_effect=fake_commit)
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    written: dict = {}

    async def fake_write(task_id, values):
        written.update(values)
        return True

    with (
        patch("app.services.crawl_runner.SessionLocal", return_value=_session_for(db)),
        patch("app.services.sync.SyncService", FakeSyncService),
        patch("app.services.crawl_runner._write_task_row", fake_write),
    ):
        with pytest.raises(RuntimeError, match="连续失败"):
            await run_crawl_task_async("task-1")

    # The task is persisted as failed through the fallback session instead of
    # silently staying "running".
    assert written["status"] == "failed"
    assert "连续失败" in written["error"]
    assert written["finished_at"] is not None
    # The progress snapshot came from memory, never from the expired ORM state.
    assert written["progress"]["next_page"] == 1
    # Counters carried from earlier attempts live in ``result``; the attempt
    # that just started reports its own (empty) progress.
    assert task.result["books_failed"] == 9
    assert task.progress["books_failed"] == 0
