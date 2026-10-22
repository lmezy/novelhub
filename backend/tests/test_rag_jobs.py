"""RAG background jobs: limits, eligibility rules and the queue runner.

The point of these tests is the guard rails: a batch is capped, the reader's
auto queue is capped per day, comics and already-indexed books are refused, and
a run stops instead of hammering a broken embedding endpoint.
"""

import asyncio
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
    rag_jobs._write({
        **rag_jobs.empty_state(),
        "queue": [{"book_id": "b1", "title": "一", "source": "batch", "max_chunks": 10}],
        "current": {"book_id": "b2", "title": "二"},
    })

    added = rag_jobs._append([
        {"book_id": "b1"},
        {"book_id": "b2"},
        {"book_id": "b3", "title": "三"},
        {"book_id": "b3", "title": "三"},
    ])

    assert added == 1
    state = rag_jobs.read_state()
    assert [entry["book_id"] for entry in state["queue"]] == ["b1", "b3"]


def test_batch_refuses_more_than_the_hard_cap():
    with pytest.raises(ValueError):
        rag_jobs.start_batch([f"b{i}" for i in range(rag_jobs.MAX_BATCH_BOOKS + 1)])


def test_batch_caps_chunks_per_book():
    rag_jobs.start_batch(["b1"], max_chunks=10_000)

    state = rag_jobs.read_state()
    assert state["queue"][0]["max_chunks"] == rag_jobs.MAX_CHUNKS_PER_BOOK


def test_status_exposes_the_limits_for_the_ui():
    limits = rag_jobs.status()["limits"]

    assert limits["max_batch_books"] == rag_jobs.MAX_BATCH_BOOKS
    assert limits["max_chunks_per_book"] == rag_jobs.MAX_CHUNKS_PER_BOOK
    assert limits["auto_daily_limit"] == rag_jobs.AUTO_DAILY_LIMIT
    assert limits["chunk_bytes_estimate"] > 0


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


def _await(value):
    async def _inner():
        return value

    return _inner()
