import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import MissingGreenlet

from app.services.crawl_runner import (
    _ActiveTask,
    _abandoned_tasks,
    _next_pending_tasks,
    _release_abandoned,
    _task_slot_budget,
    _worker_loop,
    run_crawl_task_async,
    task_concurrency_limit,
)
from app.core.database import get_db
from app.main import app
from app.services.auth import get_current_user
from app.services.sync import SyncPaused
from app.core.config import db_pool_capacity, settings


@pytest.fixture(autouse=True)
def _supervision_without_a_database():
    """Keep the worker loop's supervision out of the unit tests.

    ``_worker_loop`` re-reads the state of the tasks it runs every interval; in
    a unit test that would dial a database that is not there.  The supervisor's
    own behaviour is covered by the dedicated tests below.
    """
    with patch(
        "app.services.crawl_runner._read_active_task_states",
        new=AsyncMock(return_value={}),
    ):
        yield


def _bound_values(query) -> set:
    """Every bound value of a compiled query, flattening ``IN`` expansions."""
    values: set = set()
    for value in query.compile().params.values():
        if isinstance(value, (list, tuple, set)):
            values.update(value)
        else:
            values.add(value)
    return values


@pytest.mark.asyncio
async def test_recent_tasks_rank_every_active_task_above_finished_ones():
    """Active tasks own the top of the list, whatever else is in the table.

    The sync page asks for 100 rows.  Ranking failures just below running ones
    meant 137 failed tasks filled the entire window: a task created by the sync
    button was invisible, and pausing a running task dropped it out of the list
    (online, 2026-09-20).
    """
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

    assert {"running", "pending", "paused"} <= params
    # A finished task is not ranked at all: it falls into the CASE's ELSE
    # branch, which sorts below every active status.
    assert "failed" not in params
    assert "CASE" in sql
    assert sql.index("CASE") < sql.index("crawl_tasks.created_at DESC")

    rank = CrawlTaskRepository.STATUS_RANK
    assert set(rank) == {"running", "pending", "paused"}
    assert max(rank.values()) < len(rank)


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


def test_pool_capacity_tracks_the_configured_pool():
    """The engine really builds the pool ``db_pool_capacity`` promises.

    Long-lived holders are sized from ``db_pool_capacity``, so if the engine
    stopped using ``DB_POOL_SIZE``/``DB_MAX_OVERFLOW`` the two would drift apart
    and the clamp below would be sized against a pool that does not exist.
    """
    from app.core import database

    pool = database.engine.pool
    assert pool.size() + pool._max_overflow == db_pool_capacity()
    assert pool.size() == int(settings.DB_POOL_SIZE)
    assert pool._max_overflow == int(settings.DB_MAX_OVERFLOW)

    with patch.object(settings, "DB_POOL_SIZE", 4), patch.object(
        settings, "DB_MAX_OVERFLOW", 6
    ):
        assert database._pool_kwargs() == {"pool_size": 4, "max_overflow": 6}
        assert db_pool_capacity() == 10


def test_pool_capacity_never_reaches_zero():
    """``0/0`` (or nonsense) must not leave every caller waiting forever."""
    with patch.object(settings, "DB_POOL_SIZE", 0), patch.object(
        settings, "DB_MAX_OVERFLOW", 0
    ):
        assert db_pool_capacity() == 1
    with patch.object(settings, "DB_POOL_SIZE", "abc"), patch.object(
        settings, "DB_MAX_OVERFLOW", None
    ):
        assert db_pool_capacity() == 30


def test_task_slots_stay_below_the_pool():
    """Tasks are always fewer than the connections they will each hold.

    Every running task keeps one pooled connection for its whole life, so a task
    count at or above the pool means the surplus waits ``pool_timeout`` and then
    fails with "QueuePool limit of size 10 overflow 20 reached" -- which is
    exactly what happened online on 2026-09-19.
    """
    with patch.object(settings, "DB_POOL_SIZE", 10), patch.object(
        settings, "DB_MAX_OVERFLOW", 20
    ):
        assert _task_slot_budget() == 26
        assert _task_slot_budget() < db_pool_capacity()

    with patch.object(settings, "DB_POOL_SIZE", 3), patch.object(
        settings, "DB_MAX_OVERFLOW", 3
    ):
        assert _task_slot_budget() == 2

    # A pool too small to reserve anything still yields one usable slot.
    with patch.object(settings, "DB_POOL_SIZE", 1), patch.object(
        settings, "DB_MAX_OVERFLOW", 0
    ):
        assert _task_slot_budget() == 1


def test_task_concurrency_limit_cannot_exceed_the_pool():
    """An operator asking for more tasks than connections must not get them."""
    with patch.object(settings, "DB_POOL_SIZE", 10), patch.object(
        settings, "DB_MAX_OVERFLOW", 20
    ):
        # Default: "one worker per source", bounded by the pool.
        with patch.object(settings, "SYNC_WORKER_CONCURRENCY", 0):
            assert task_concurrency_limit() == 26
        # A ceiling below the budget is honoured.
        with patch.object(settings, "SYNC_WORKER_CONCURRENCY", 5):
            assert task_concurrency_limit() == 5
        # A ceiling above the budget is clamped down to it.  This is the
        # regression: it used to return the number asked for (or a flat 32)
        # regardless of how many connections the pool could actually serve.
        with patch.object(settings, "SYNC_WORKER_CONCURRENCY", 100):
            assert task_concurrency_limit() == 26


@pytest.mark.asyncio
async def test_worker_loop_starts_no_more_tasks_than_the_pool_can_serve():
    """The loop itself must stop at the budget, not at the requested batch.

    Reproduces the online failure shape: many sources pending, a pool that can
    serve far fewer.  Before the clamp the loop started a flat batch of 32.
    """
    pending = [(f"task-{i}", f"src-{i}") for i in range(40)]
    started: list[str] = []
    release = asyncio.Event()
    filled = asyncio.Event()

    async def fake_next(limit, exclude_sources=None):
        running = {s for _, s in pending if s in (exclude_sources or set())}
        ready = [pair for pair in pending if pair[1] not in running]
        return ready[:limit]

    async def fake_run(task_id: str) -> dict:
        started.append(task_id)
        if len(started) >= budget:
            filled.set()
        await release.wait()
        return {"status": "completed", "task_id": task_id}

    with patch.object(settings, "DB_POOL_SIZE", 3), patch.object(
        settings, "DB_MAX_OVERFLOW", 3
    ), patch.object(settings, "SYNC_WORKER_CONCURRENCY", 0):
        budget = task_concurrency_limit()
        assert budget == 2
        with (
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
                await asyncio.wait_for(filled.wait(), timeout=5)
                # Give the loop room to over-schedule if it is going to.
                await asyncio.sleep(0.3)
            finally:
                release.set()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

    assert len(started) == budget


# ---------------------------------------------------------------------------
# Supervision: a slot must never be held by a task that is not running any more.
#
# Online on 2026-09-20 the queue stopped consuming anything for 12 hours: every
# slot was held by a task the user had already paused or cancelled, whose sync
# never reached a checkpoint to notice, so nine freshly created tasks stayed
# ``pending`` while the worker sat idle in epoll_wait.
# ---------------------------------------------------------------------------


def _entry(task_id="task-a", source="yuedu_a", now=0.0) -> _ActiveTask:
    return _ActiveTask(task_id, source, now)


def _states(task_id, status, *, progress=None, extra=None):
    states = {task_id: {"status": status, "progress": progress or {}}}
    states.update(extra or {})
    return states


def test_abandoned_tasks_gives_a_paused_task_its_grace_then_takes_it_back():
    entry = _entry()
    active = {"slot": entry}

    # First sighting: the row says paused, so the sync gets its grace period to
    # reach its own checkpoint (which is where it saves ``next_page``).
    assert _abandoned_tasks(
        active, _states("task-a", "paused"), now=100.0, stop_grace=120.0,
        stall_seconds=0,
    ) == []
    assert entry.stop_seen_at == 100.0

    assert _abandoned_tasks(
        active, _states("task-a", "paused"), now=219.0, stop_grace=120.0,
        stall_seconds=0,
    ) == []

    abandoned = _abandoned_tasks(
        active, _states("task-a", "paused"), now=220.0, stop_grace=120.0,
        stall_seconds=0,
    )
    assert [(e.task_id, reason) for _, e, reason in abandoned] == [("task-a", "paused")]


def test_abandoned_tasks_never_touches_a_running_task_that_reports_progress():
    """A healthy task's changing progress must keep resetting the watchdog."""
    entry = _entry()
    active = {"slot": entry}

    for step in range(6):
        now = 1000.0 * step
        abandoned = _abandoned_tasks(
            active,
            _states("task-a", "running", progress={"pages_checked": step}),
            now=now,
            stop_grace=0.0,
            stall_seconds=600.0,
        )
        assert abandoned == []
        assert entry.marker == (step, None, None, None, None, None)
    assert entry.stop_seen_at is None


def test_abandoned_tasks_fails_a_task_that_stopped_reporting_progress():
    entry = _entry(now=0.0)
    active = {"slot": entry}
    frozen = {"status": "running", "progress": {"pages_checked": 3}}

    # The first look records the heartbeat; the clock starts from there.
    assert _abandoned_tasks(
        active, {"task-a": frozen}, now=100.0, stop_grace=0.0, stall_seconds=3600.0
    ) == []
    assert _abandoned_tasks(
        active, {"task-a": frozen}, now=3699.0, stop_grace=0.0, stall_seconds=3600.0
    ) == []
    abandoned = _abandoned_tasks(
        active, {"task-a": frozen}, now=3700.0, stop_grace=0.0, stall_seconds=3600.0
    )
    assert [(e.task_id, reason) for _, e, reason in abandoned] == [("task-a", "stalled")]

    # ``0`` disables the watchdog: a deliberately unlimited full-site sync is
    # then allowed to report nothing for as long as the operator wants.
    assert _abandoned_tasks(
        active, {"task-a": frozen}, now=10**9, stop_grace=0.0, stall_seconds=0.0
    ) == []


def test_abandoned_tasks_leaves_a_pending_row_alone():
    """A batch-mode task re-queues itself as ``pending`` before it returns.

    A task that has just been started looks the same for the moment before it
    commits ``running``, so neither may be cancelled -- even long after the
    stall timeout would have fired for a task that had really gone quiet.
    """
    entry = _entry(now=0.0)
    active = {"slot": entry}
    pending = {"status": "pending", "progress": {}}

    assert _abandoned_tasks(
        active, {"task-a": pending}, now=10.0, stop_grace=0.0, stall_seconds=5.0
    ) == []
    assert _abandoned_tasks(
        active, {"task-a": pending}, now=10**6, stop_grace=0.0, stall_seconds=5.0
    ) == []


def test_abandoned_tasks_reclaims_a_deleted_task():
    entry = _entry()
    assert _abandoned_tasks(
        {"slot": entry}, {}, now=0.0, stop_grace=120.0, stall_seconds=0.0
    ) == []
    abandoned = _abandoned_tasks(
        {"slot": entry}, {}, now=120.0, stop_grace=120.0, stall_seconds=0.0
    )
    assert [(e.task_id, reason) for _, e, reason in abandoned] == [("task-a", "deleted")]


def test_release_abandoned_hands_back_the_slot_source_and_claim():
    entry = _entry()
    entry.abandoned_at = 100.0
    active = {"slot": entry}
    running_sources = {"yuedu_a"}
    claimed = {"task-a"}

    assert _release_abandoned(
        active, running_sources, claimed, now=219.0, grace=120.0
    ) == []
    assert set(active) == {"slot"}

    released = _release_abandoned(
        active, running_sources, claimed, now=220.0, grace=120.0
    )
    assert released == ["task-a"]
    assert active == {}
    assert running_sources == set()
    assert claimed == set()
    # The zombie's own cleanup must not discard a source a new task now owns.
    assert entry.released is True


@pytest.mark.asyncio
async def test_worker_loop_stops_a_paused_task_and_lets_its_source_run_again():
    """The online deadlock, end to end: pause must free the source it holds.

    The task is stuck in a network wait, exactly like the 15 paused full-site
    tasks whose sync never reached a checkpoint: nothing in the sync itself will
    ever notice the pause, so only the supervisor can take the source back.
    """
    started: list[str] = []
    excluded: list[set[str]] = []
    served: list[str] = []
    second_started = asyncio.Event()
    stuck = asyncio.Event()

    async def fake_next(limit, exclude_sources=None):
        excluded.append(set(exclude_sources or ()))
        if "yuedu_a" in (exclude_sources or set()):
            # The first task still owns the source: this is exactly what used
            # to leave newly created tasks queued forever.
            return []
        if not served:
            served.append("task-a")
            return [("task-a", "yuedu_a")]
        return [("task-b", "yuedu_a")]

    async def fake_run(task_id: str) -> dict:
        started.append(task_id)
        if task_id == "task-b":
            second_started.set()
            return {"status": "completed", "task_id": task_id}
        await stuck.wait()
        return {"status": "paused", "task_id": task_id}

    async def fake_states(task_ids):
        return {
            task_id: {
                "status": "paused" if task_id == "task-a" else "running",
                "progress": {},
            }
            for task_id in task_ids
        }

    with (
        patch.object(settings, "SYNC_WORKER_CONCURRENCY", 0),
        patch.object(settings, "SYNC_TASK_SUPERVISE_INTERVAL_SECONDS", 1),
        patch.object(settings, "SYNC_TASK_STOP_GRACE_SECONDS", 1),
        patch(
            "app.services.crawl_runner._next_pending_tasks", side_effect=fake_next
        ),
        patch(
            "app.services.crawl_runner.run_crawl_task_async", side_effect=fake_run
        ),
        patch(
            "app.services.crawl_runner._read_active_task_states",
            side_effect=fake_states,
        ),
    ):
        worker = asyncio.create_task(_worker_loop())
        try:
            await asyncio.wait_for(second_started.wait(), timeout=8)
        finally:
            stuck.set()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    assert started[:2] == ["task-a", "task-b"]
    # After the pause took the first task off the queue the source is offered
    # again instead of being filtered out forever.
    assert set() in excluded


# ---------------------------------------------------------------------------
# Deleting tasks: finished ones used to pile up with no way to remove them.
# ---------------------------------------------------------------------------


async def _delete_request(task, path="/api/crawl/tasks/task-1", role="admin",
                          user_id="admin", rowcount=1):
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    executed: list = []

    async def fake_execute(statement):
        executed.append(statement)
        return SimpleNamespace(rowcount=rowcount)

    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=user_id, role=role
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(path)
    finally:
        app.dependency_overrides.clear()
    return resp, executed


@pytest.mark.asyncio
async def test_delete_task_removes_the_task_and_its_logs():
    resp, executed = await _delete_request(_task(status="failed"))

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1}
    # ``crawl_logs`` has no foreign key, so its rows have to go first or they
    # are orphaned forever.
    assert [statement.table.name for statement in executed] == [
        "crawl_logs",
        "crawl_tasks",
    ]


@pytest.mark.asyncio
async def test_delete_task_is_refused_while_it_is_still_queued():
    for status in ("pending", "running"):
        resp, executed = await _delete_request(_task(status=status))
        assert resp.status_code == 400
        assert "Cancel the task" in resp.json()["detail"]
        assert executed == []


@pytest.mark.asyncio
async def test_delete_task_404s_for_another_users_task():
    task = _task(status="failed", user_id="someone-else")
    resp, executed = await _delete_request(task, role="user", user_id="u1")
    assert resp.status_code == 404
    assert executed == []


@pytest.mark.asyncio
async def test_clear_history_only_ever_touches_finished_tasks():
    db = AsyncMock()
    captured: dict = {}

    async def fake_scalars(query):
        captured["query"] = query
        return SimpleNamespace(all=lambda: ["t1", "t2", "t3"])

    db.scalars = fake_scalars
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=3))
    db.commit = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="u1", role="user"
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/crawl/tasks/clear-history")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 3}

    compiled = captured["query"].compile()
    params = _bound_values(captured["query"])
    assert {"completed", "failed", "cancelled", "completed_with_errors"} <= params
    # A task that is still queued, running or parked for later is not history.
    assert not {"pending", "running", "paused"} & params
    # A non-admin clears their own history only, exactly like the list they see.
    assert "crawl_tasks.user_id" in str(compiled)


