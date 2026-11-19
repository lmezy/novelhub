"""Manual RAG batches, reader-triggered limits, and the queue runner."""

import asyncio
import os
import time
from datetime import date
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import rag_jobs


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    yield


def fake_config(**overrides):
    values = {
        "rag_auto_index": True,
        "embeddings_supported": True,
        "effective_embedding_model": "bge-m3",
        "rag_enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_book(book_id="b1", title="测试书", kind="novel"):
    return SimpleNamespace(id=book_id, title=title, kind=kind)


class FakeDB:
    """Returns the ``scalar`` answers in order (chunks, then chapters)."""

    def __init__(self, *answers):
        self.answers = list(answers)

    async def scalar(self, statement):
        return self.answers.pop(0) if self.answers else 0


# ---------------------------------------------------------------------------
# queue mechanics
# ---------------------------------------------------------------------------


def test_append_skips_books_already_queued_or_running():
    state = {
        **rag_jobs.empty_state(),
        "queue": [{"book_id": "b1", "title": "一", "source": "batch", "max_chunks": 10}],
        "current": {"book_id": "b2", "title": "二"},
    }

    added = rag_jobs._append(state, [
        {"book_id": "b1"},
        {"book_id": "b2"},
        {"book_id": "b3", "title": "三"},
        {"book_id": "b3", "title": "三"},
    ])

    assert added == 1
    assert [entry["book_id"] for entry in state["queue"]] == ["b1", "b3"]


@pytest.mark.parametrize("max_chunks", [0, 10_000])
def test_batch_preserves_limits_above_old_cap(max_chunks, monkeypatch):
    monkeypatch.setattr(rag_jobs, "ensure_worker", lambda: False)
    result = rag_jobs.start_batch(
        [f"b{i}" for i in range(1001)], max_chunks=max_chunks, include_queue=False,
    )
    assert result["queued"] == 1001
    assert result["max_chunks"] == max_chunks
    assert result["state"]["queued"] == 1001
    assert "queue" not in result["state"]
    assert rag_jobs.status()["queue"][-1]["max_chunks"] == max_chunks


def test_status_exposes_the_limits_for_the_ui():
    limits = rag_jobs.status()["limits"]

    assert limits["default_batch_chunks"] == rag_jobs.DEFAULT_BATCH_CHUNKS
    assert limits["auto_daily_limit"] == rag_jobs.AUTO_DAILY_LIMIT
    assert limits["chunk_bytes_estimate"] > 0


def test_long_running_job_keeps_its_worker_lock(monkeypatch):
    lock_path = rag_jobs._lock_path()
    assert rag_jobs.acquire_file_lock(lock_path)
    stale_at = time.time() - rag_jobs.LOCK_STALE_SECONDS - 1
    os.utime(lock_path, (stale_at, stale_at))
    monkeypatch.setattr(rag_jobs, "_lock_predates_process", lambda path, pid: False)

    assert not rag_jobs.acquire_file_lock(lock_path)


@pytest.mark.parametrize("phase", ["done", "failed", "cancelled", "indexing"])
def test_next_batch_resets_previous_run_and_preserves_auto_count(phase, monkeypatch):
    monkeypatch.setattr(rag_jobs, "ensure_worker", lambda: False)
    rag_jobs._write({
        **rag_jobs.empty_state(),
        "phase": phase,
        "running": phase == "indexing",
        "started_at": "2026-10-01T07:00:00",
        "total": 3000,
        "done": 10,
        "failed": 3,
        "chunks": 2000,
        "recent_errors": [{"error": "previous failure"}],
        "error": "previous batch stopped",
        "queue": [{"book_id": "old-pending"}],
        "auto_date": "2026-10-01",
        "auto_count": 4,
    })

    old = rag_jobs.status()
    assert old["phase"] == ("interrupted" if phase == "indexing" else phase)
    assert old["queued"] == 0
    assert old["discarded"] == 1
    assert old["total"] == 13

    state = rag_jobs.start_batch(["new"])["state"]

    assert [item["book_id"] for item in state["queue"]] == ["new"]
    assert state["total"] == 1
    assert state["started_at"] is None
    assert state["done"] == state["failed"] == state["chunks"] == state["discarded"] == 0
    assert state["recent_errors"] == []
    assert state["error"] is None
    assert state["auto_date"] == "2026-10-01"
    assert state["auto_count"] == 4


def test_live_batch_reconciles_inherited_total_with_actual_queue(monkeypatch):
    assert rag_jobs.acquire_file_lock(rag_jobs._lock_path())
    rag_jobs._write({
        **rag_jobs.empty_state(),
        "running": True,
        "phase": "indexing",
        "total": 15,
        "done": 3,
        "failed": 2,
        "skipped": 1,
        "current": {"book_id": "current"},
        "queue": [{"book_id": "old-pending"}],
    })

    status = rag_jobs.status()
    assert status["phase"] == "indexing"
    assert status["total"] == 8
    assert status["discarded"] == 0

    monkeypatch.setattr(rag_jobs, "ensure_worker", lambda: False)
    state = rag_jobs.start_batch(["new-pending"])["state"]
    assert state["total"] == 9
    assert state["queued"] == 2


def test_reused_worker_pid_is_marked_interrupted(monkeypatch):
    assert rag_jobs.acquire_file_lock(rag_jobs._lock_path())
    rag_jobs._write({
        **rag_jobs.empty_state(),
        "running": True,
        "phase": "indexing",
        "total": 1,
        "current": {"book_id": "in-progress"},
    })
    monkeypatch.setattr(rag_jobs, "_lock_predates_process", lambda path, pid: True)

    state = rag_jobs.status()
    assert state["phase"] == "interrupted"
    assert state["running"] is False
    assert state["total"] == 0
    assert state["discarded"] == 1


# ---------------------------------------------------------------------------
# "打开即建" eligibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_refused_when_the_setting_is_off(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_config.get_ai_config",
        lambda db: _await(fake_config(rag_auto_index=False)),
    )

    result = await rag_jobs.request_auto(FakeDB(), fake_book())

    assert result["queued"] is False
    assert "未开启" in result["reason"]


@pytest.mark.asyncio
async def test_auto_refused_without_an_embedding_endpoint(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_config.get_ai_config",
        lambda db: _await(fake_config(embeddings_supported=False)),
    )

    result = await rag_jobs.request_auto(FakeDB(), fake_book())

    assert result["queued"] is False
    assert "向量服务未配置" in result["reason"]


@pytest.mark.asyncio
async def test_auto_refused_when_semantic_search_is_off(monkeypatch):
    """Indexing without RAG in chat only spends quota and space."""
    monkeypatch.setattr(
        "app.services.ai_config.get_ai_config",
        lambda db: _await(fake_config(rag_enabled=False)),
    )

    result = await rag_jobs.request_auto(FakeDB(), fake_book())

    assert result["queued"] is False
    assert "语义检索" in result["reason"]


@pytest.mark.asyncio
async def test_auto_refuses_comics(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_config.get_ai_config",
        lambda db: _await(fake_config()),
    )

    result = await rag_jobs.request_auto(FakeDB(), fake_book(kind="comic"))

    assert result["queued"] is False
    assert "漫画" in result["reason"]


@pytest.mark.asyncio
async def test_auto_refuses_an_already_indexed_book(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_config.get_ai_config",
        lambda db: _await(fake_config()),
    )

    result = await rag_jobs.request_auto(FakeDB(5), fake_book())

    assert result["queued"] is False
    assert "已经有向量索引" in result["reason"]


@pytest.mark.asyncio
async def test_auto_refuses_a_book_without_chapters(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_config.get_ai_config",
        lambda db: _await(fake_config()),
    )

    result = await rag_jobs.request_auto(FakeDB(0, 0), fake_book())

    assert result["queued"] is False
    assert "没有可读章节" in result["reason"]


@pytest.mark.asyncio
async def test_auto_respects_the_daily_limit(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_config.get_ai_config",
        lambda db: _await(fake_config()),
    )
    rag_jobs._write({
        **rag_jobs.empty_state(),
        "auto_date": date.today().isoformat(),
        "auto_count": rag_jobs.AUTO_DAILY_LIMIT,
    })

    result = await rag_jobs.request_auto(FakeDB(0, 10), fake_book())

    assert result["queued"] is False
    assert "上限" in result["reason"]


@pytest.mark.asyncio
async def test_auto_enqueues_a_novel_once(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_config.get_ai_config",
        lambda db: _await(fake_config()),
    )
    monkeypatch.setattr(rag_jobs, "ensure_worker", lambda: False)

    result = await rag_jobs.request_auto(FakeDB(0, 10), fake_book("b9", "第九本"))

    assert result["queued"] is True
    state = rag_jobs.read_state()
    assert state["queue"][0]["book_id"] == "b9"
    assert state["queue"][0]["source"] == "auto"
    assert state["queue"][0]["max_chunks"] == rag_jobs.DEFAULT_AUTO_CHUNKS
    assert state["auto_count"] == 1

    again = await rag_jobs.request_auto(FakeDB(0, 10), fake_book("b9", "第九本"))
    assert again["queued"] is False
    # A repeated open must not spend a second slot of the daily budget.
    assert rag_jobs.read_state()["auto_count"] == 1


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_drains_the_queue_and_reports(monkeypatch):
    rag_jobs._write({
        **rag_jobs.empty_state(),
        "running": True,
        "total": 2,
        "queue": [
            {"book_id": "b1", "title": "一", "source": "batch", "max_chunks": 10},
            {"book_id": "b2", "title": "二", "source": "batch", "max_chunks": 10},
        ],
    })

    async def fake_index(item):
        return {"skipped": False, "chunks": 3}

    monkeypatch.setattr(rag_jobs, "_index_one", fake_index)

    await rag_jobs._run()

    state = rag_jobs.read_state()
    assert state["running"] is False
    assert state["phase"] == "done"
    assert state["done"] == 2
    assert state["chunks"] == 6
    assert state["queue"] == []


@pytest.mark.asyncio
async def test_runner_stops_after_repeated_failures(monkeypatch):
    rag_jobs._write({
        **rag_jobs.empty_state(),
        "running": True,
        "total": 5,
        "queue": [
            {"book_id": f"b{i}", "title": f"第{i}本", "source": "batch", "max_chunks": 10}
            for i in range(5)
        ],
    })

    async def broken(item):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(rag_jobs, "_index_one", broken)

    await rag_jobs._run()

    state = rag_jobs.read_state()
    assert state["phase"] == "failed"
    assert state["failed"] == rag_jobs.MAX_CONSECUTIVE_FAILURES
    assert state["total"] == state["failed"]
    assert state["discarded"] == 5 - state["failed"]
    assert state["queue"] == []
    assert "429" in state["error"]
    # The remaining books stay unsent; they are not retried in a tight loop.
    assert state["done"] == 0


@pytest.mark.asyncio
async def test_cancel_stops_between_books(monkeypatch):
    rag_jobs._write({
        **rag_jobs.empty_state(),
        "running": True,
        "total": 2,
        "cancel_requested": True,
        "queue": [
            {"book_id": "b1", "title": "一", "source": "batch", "max_chunks": 10},
            {"book_id": "b2", "title": "二", "source": "batch", "max_chunks": 10},
        ],
    })

    async def fake_index(item):
        return {"skipped": False, "chunks": 1}

    monkeypatch.setattr(rag_jobs, "_index_one", fake_index)

    await rag_jobs._run()

    state = rag_jobs.read_state()
    assert state["phase"] == "cancelled"
    assert state["done"] == 0
    assert state["total"] == 0
    assert state["discarded"] == 2


def _await(value):
    async def _inner():
        return value

    return _inner()
