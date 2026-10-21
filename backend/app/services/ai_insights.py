"""Saved AI analyses per book, reused when a similar question comes back.

Only the *same book* is searched: an analysis of "这本书的主角动机是什么" is
worth reusing for that book, not for another one.  The matcher is a character
bigram Jaccard over the normalised question -- deterministic, offline, and good
enough for "ask the same thing again"; swapping in embeddings later means
changing ``similarity()`` only.
"""
from __future__ import annotations

import re
from uuid import uuid4
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import naive_now
from app.models import AIBookInsight, Book, Chapter, User
from loguru import logger

#: How many past analyses are injected into one prompt.
PRIOR_LIMIT = 3
#: How much of a prior answer is carried into the prompt.
PRIOR_ANSWER_CHARS = 600
#: Questions at or above this similarity are treated as "asked before".
SAME_QUESTION = 0.8
#: Questions at or above this similarity are considered related enough to cite.
RELATED_QUESTION = 0.3
#: Rows kept per book; the oldest unused ones are pruned.
MAX_INSIGHTS_PER_BOOK = 200

_PUNCT = re.compile(r"[\s，。！？、；：“”‘’（）【】《》,.!?;:'\"()\[\]<>~～—-]+")


def normalize_question(text: str) -> str:
    return _PUNCT.sub("", str(text or "").lower())


def _bigrams(text: str) -> set[str]:
    normalized = normalize_question(text)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[i:i + 2] for i in range(len(normalized) - 1)}


def similarity(left: str, right: str) -> float:
    """Jaccard over character bigrams (0..1).

    CJK has no spaces, so a bigram overlap is the cheapest deterministic
    measure: "主角是谁" against "这本书的主角是谁" scores 0.43 -- related -- while
    an unrelated question stays below 0.15.
    """
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def prior_block(insights: Sequence[AIBookInsight]) -> str:
    """The prompt section handed to the model with earlier analyses."""
    if not insights:
        return ""
    lines = [
        "以下是此前针对**这本书**做过的分析，可能与本次问题相关：",
    ]
    for index, insight in enumerate(insights, start=1):
        answer = " ".join(str(insight.answer or "").split())
        if len(answer) > PRIOR_ANSWER_CHARS:
            answer = answer[:PRIOR_ANSWER_CHARS] + "…"
        flag = "（原文该章后来有更新，请核对）" if insight.stale else ""
        lines.append(f"{index}. 问：{insight.question}\n   答：{answer}{flag}")
    lines.append(
        "请在已有分析的基础上回答本次问题：与之前的结论一致时可以直接复用，"
        "不一致或原文更新时以原文为准并指出差异，不要重复整段旧答案。"
    )
    return "\n".join(lines)


async def related_insights(
    db: AsyncSession,
    book_id: str,
    question: str,
    *,
    limit: int = PRIOR_LIMIT,
) -> list[AIBookInsight]:
    """Past analyses of this book that are worth putting back into the prompt."""
    rows = list(
        (
            await db.scalars(
                select(AIBookInsight)
                .where(AIBookInsight.book_id == book_id)
                .order_by(AIBookInsight.updated_at.desc())
                .limit(MAX_INSIGHTS_PER_BOOK)
            )
        ).all()
    )
    scored = [(similarity(question, row.question), row) for row in rows]
    scored = [pair for pair in scored if pair[0] >= RELATED_QUESTION]
    scored.sort(key=lambda pair: (pair[0], pair[1].hit_count), reverse=True)
    chosen = [row for _score, row in scored[: max(1, limit)]]
    for row in chosen:
        row.hit_count = int(row.hit_count or 0) + 1
    if chosen:
        await db.commit()
    return chosen


async def remember_insight(
    db: AsyncSession,
    *,
    book_id: str,
    question: str,
    answer: str,
    chapter_number: int | None = None,
    user: User | None = None,
    answered_by: str = "",
) -> AIBookInsight | None:
    """Store one question/answer pair, merging a repeat of the same question.

    Never raises into the request path: a book that cannot be remembered must not
    fail the answer the reader is waiting for.
    """
    question = " ".join(str(question or "").split())
    answer = str(answer or "").strip()
    if not question or not answer:
        return None
    try:
        chapter_id = None
        if chapter_number is not None:
            chapter_id = await db.scalar(
                select(Chapter.id).where(
                    Chapter.book_id == book_id,
                    Chapter.chapter_number == chapter_number,
                )
            )
        existing = list(
            (
                await db.scalars(
                    select(AIBookInsight).where(AIBookInsight.book_id == book_id)
                )
            ).all()
        )
        same = next(
            (row for row in existing if similarity(question, row.question) >= SAME_QUESTION),
            None,
        )
        if same is not None:
            same.question = question
            same.answer = answer
            same.chapter_id = chapter_id or same.chapter_id
            same.updated_at = naive_now()
            same.stale = False
            same.answered_by = answered_by or same.answered_by
            await db.commit()
            return same
        insight = AIBookInsight(
            id=str(uuid4()),
            book_id=book_id,
            chapter_id=chapter_id,
            user_id=getattr(user, "id", None),
            question=question,
            answer=answer,
            answered_by=answered_by,
            hit_count=0,
            stale=False,
        )
        db.add(insight)
        await db.commit()
        if len(existing) + 1 > MAX_INSIGHTS_PER_BOOK:
            oldest = sorted(
                existing, key=lambda row: (int(row.hit_count or 0), row.updated_at or naive_now())
            )[: max(1, len(existing) + 1 - MAX_INSIGHTS_PER_BOOK)]
            for row in oldest:
                await db.execute(delete(AIBookInsight).where(AIBookInsight.id == row.id))
            await db.commit()
        return insight
    except Exception as exc:  # noqa: BLE001 - remembering must never break chat
        logger.warning("Could not remember an AI insight: {}", exc)
        await db.rollback()
        return None


async def mark_chapter_insights_stale(db: AsyncSession, chapter_id: str) -> None:
    """Flag analyses that quoted a chapter which is about to be replaced."""
    from sqlalchemy import update

    try:
        await db.execute(
            update(AIBookInsight)
            .where(AIBookInsight.chapter_id == chapter_id)
            .values(stale=True)
        )
    except Exception as exc:  # noqa: BLE001 - the sync must not fail over this
        logger.debug("Could not flag stale AI insights: {}", exc)


async def list_insights(
    db: AsyncSession, book_id: str, *, limit: int = 100
) -> list[AIBookInsight]:
    return list(
        (
            await db.scalars(
                select(AIBookInsight)
                .where(AIBookInsight.book_id == book_id)
                .order_by(AIBookInsight.updated_at.desc())
                .limit(max(1, min(limit, MAX_INSIGHTS_PER_BOOK)))
            )
        ).all()
    )


async def visible_book(db: AsyncSession, book_id: str) -> Book | None:
    return await db.get(Book, book_id)
