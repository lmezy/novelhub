"""Saved AI analyses per book: matching, prompt injection, remembering."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai_insights import (
    RELATED_QUESTION,
    SAME_QUESTION,
    normalize_question,
    prior_block,
    related_insights,
    remember_insight,
    similarity,
)


class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeDB:
    """Enough of an AsyncSession for the insights service."""

    def __init__(self, rows=None, chapter_id=None):
        self.rows = list(rows or [])
        self.chapter_id = chapter_id
        self.commits = 0
        self.deleted = []

    async def scalars(self, _statement):
        return FakeScalars(self.rows)

    async def scalar(self, _statement):
        return self.chapter_id

    def add(self, obj):
        self.rows.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None

    async def execute(self, _statement):
        return None

    async def delete(self, obj):
        self.deleted.append(obj)


def insight(question, answer="答案", **overrides):
    values = {
        "id": "i1",
        "book_id": "b1",
        "chapter_id": None,
        "user_id": "u1",
        "question": question,
        "answer": answer,
        "answered_by": "m",
        "hit_count": 0,
        "stale": False,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_similarity_separates_related_from_unrelated():
    assert similarity("主角是谁", "主角是谁") == 1.0
    assert similarity("这本书的主角是谁", "主角是谁") >= RELATED_QUESTION
    assert similarity("结局是什么意思", "主角是谁") < RELATED_QUESTION
    assert normalize_question(" 主角，是谁？ ") == "主角是谁"


def test_prior_block_carries_questions_answers_and_staleness():
    block = prior_block([
        insight("主角的动机是什么", "他为复仇而来。" * 100),
        insight("结局如何", "开放式。", stale=True),
    ])

    assert "主角的动机是什么" in block
    assert "他为复仇而来。" in block
    assert "…" in block                       # long answers are trimmed
    assert "原文该章后来有更新" in block       # stale ones are flagged
    assert prior_block([]) == ""


@pytest.mark.asyncio
async def test_related_insights_only_returns_this_books_close_questions():
    rows = [
        insight("主角是谁", hit_count=1),
        insight("完全无关的问题", answer="x"),
    ]
    db = FakeDB(rows)

    found = await related_insights(db, "b1", "这本书的主角是谁？")

    assert [row.question for row in found] == ["主角是谁"]
    assert found[0].hit_count == 2              # reuse is counted
    assert db.commits == 1


@pytest.mark.asyncio
async def test_remember_insight_merges_a_repeat_and_inserts_a_new_question():
    existing = insight("主角是谁", answer="旧答案")
    db = FakeDB([existing])

    same = await remember_insight(db, book_id="b1", question="主角是谁？", answer="新答案")
    assert same is existing
    assert existing.answer == "新答案"
    assert len(db.rows) == 1                     # merged, not duplicated

    fresh = await remember_insight(db, book_id="b1", question="结局怎样", answer="开放式")
    assert fresh is not None and fresh.question == "结局怎样"
    assert len(db.rows) == 2


@pytest.mark.asyncio
async def test_remember_insight_never_raises():
    class BrokenDB(FakeDB):
        async def scalars(self, _statement):
            raise RuntimeError("db down")

    assert await remember_insight(BrokenDB(), book_id="b1", question="q", answer="a") is None


@pytest.mark.asyncio
async def test_chat_injects_saved_analyses_and_remembers_the_answer():
    """The reader's question goes out with this book's prior analyses attached."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.services.auth import get_current_user
    from app.core.database import get_db

    service = MagicMock()
    service.chat = AsyncMock(return_value={"answer": "新的回答", "model": "deepseek-flash"})

    prior = insight("主角的动机是什么", answer="为了复仇。")

    with patch("app.api.routes.ai.AIService", return_value=service), \
         patch("app.api.routes.ai._require_visible_book", AsyncMock()), \
         patch("app.api.routes.ai.related_insights", AsyncMock(return_value=[prior])), \
         patch("app.api.routes.ai.remember_insight", AsyncMock()) as remembered:
        app.dependency_overrides[get_db] = lambda: FakeDB()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id="u1", role="user", r18_enabled=True, non_r18_enabled=True,
        )
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/ai/chat",
                    json={"book_id": "b1", "message": "主角为什么复仇"},
                )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 200
    sent = service.chat.await_args.kwargs["message"]
    assert "主角的动机是什么" in sent             # prior analysis fed back
    assert "读者当前的提问" in sent
    assert sent.rstrip().endswith("主角为什么复仇")
    remembered.assert_awaited_once()
    assert remembered.await_args.kwargs["answer"] == "新的回答"
