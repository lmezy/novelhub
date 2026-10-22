"""Book profiles (cross-book discovery): text building and job guard rails."""

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import book_profiles, library_jobs, rag_jobs


def fake_book(**overrides):
    values = {
        "id": "b1",
        "title": "测试书名",
        "author_name": "某作者",
        "tag_names": ["都市", "r18"],
        "category_names": ["都市"],
        "description": "一句话简介",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_chapters(count, size=0):
    return [
        SimpleNamespace(chapter_number=i + 1, content_path=f"p{i + 1}", size=size)
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# profile text
# ---------------------------------------------------------------------------


def test_pick_chapters_evenly_spreads_across_the_book():
    assert book_profiles.pick_chapters(list("abcdefghij"), 4) == ["a", "d", "g", "j"]
    assert book_profiles.pick_chapters(list("abc"), 4) == ["a", "b", "c"]


def test_segment_texts_samples_four_chapters_and_prefixes_metadata():
    book = fake_book()
    chapters = fake_chapters(12)
    read = lambda path: f"{path}\n" + "正" * 1200  # noqa: E731

    texts = book_profiles.segment_texts(book, chapters, read)

    assert len(texts) == 4
    for text in texts:
        assert text.startswith("《测试书名》")
        assert "作者：某作者" in text
        assert "标签：都市、r18" in text
        # SEGMENT_CHARS of body + the metadata line.
        assert text.count("正") >= book_profiles.SEGMENT_CHARS - 10
    # Evenly sampled: first, ~1/3, ~2/3, last.
    assert "p1" in texts[0] and "p12" in texts[-1]


def test_short_book_is_split_into_segments():
    book = fake_book(tag_names=[], category_names=[], description="")
    chapters = fake_chapters(1)
    read = lambda path: "甲" * 4000  # noqa: E731

    texts = book_profiles.segment_texts(book, chapters, read)

    assert len(texts) == 4
    assert all(text.count("甲") <= book_profiles.SEGMENT_CHARS for text in texts)
    assert sum(text.count("甲") for text in texts) >= 3000


def test_unreadable_chapters_yield_no_profile():
    book = fake_book()
    chapters = fake_chapters(3)

    def read(path):
        raise OSError("gone")

    assert book_profiles.segment_texts(book, chapters, read) == []


def test_vector_literal_round_trip():
    literal = book_profiles.vector_literal([0.5, -1.25, 3.0])
    assert literal == "[0.5000000,-1.2500000,3.0000000]"
    assert book_profiles.parse_vector_literal(literal) == [0.5, -1.25, 3.0]
    assert book_profiles.parse_vector_literal("") == []


# ---------------------------------------------------------------------------
# build job
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    yield


def test_status_exposes_the_run_limits():
    limits = library_jobs.status()["limits"]
    assert limits["max_books_per_run"] == library_jobs.MAX_BOOKS_PER_RUN
    assert limits["books_per_request"] == library_jobs.BOOKS_PER_REQUEST


def test_start_refuses_while_the_rag_queue_is_running():
    rag_jobs._write({**rag_jobs.empty_state(), "running": True})

    with pytest.raises(ValueError):
        library_jobs.start(limit=10)


@pytest.mark.asyncio
async def test_start_clamps_the_limit_and_writes_running_state(monkeypatch):
    monkeypatch.setattr(library_jobs, "ensure_worker", lambda: True)

    state = library_jobs.start(limit=999_999)

    assert state["running"] is True
    assert state["limit"] == library_jobs.MAX_BOOKS_PER_RUN
    assert library_jobs.read_state()["phase"] == "running"


@pytest.mark.asyncio
async def test_cancel_only_marks_a_running_job(monkeypatch):
    monkeypatch.setattr(library_jobs, "ensure_worker", lambda: True)
    library_jobs.start(limit=5)

    state = library_jobs.cancel()

    assert state["cancel_requested"] is True
    assert "取消" in state["message"]

    idle = library_jobs.empty_state()
    library_jobs._write(idle)
    assert library_jobs.cancel()["cancel_requested"] is False
