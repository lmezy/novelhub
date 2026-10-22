"""One small vector set per book: cross-book discovery ("找相似的书").

``chapter_embeddings`` answers questions *inside* one book and costs thousands of
vectors per book.  Discovery has the opposite shape: a reader pastes a plot
fragment and wants **other books** like it, so one compact profile per book is
enough -- and a whole-library chapter-level index (70-100 GB, hundreds of
thousands of API calls) is not.

Each book gets :data:`PROFILE_SEGMENTS` excerpts (evenly sampled chapters, or a
single short book split into slices), each embedded separately.  Four vectors
rather than one keeps specific plot beats, and the matched excerpt can be shown
back to the reader as "this is the part that matched".

Vectors live in Postgres through pgvector (``book_profiles``, 1024 dimensions,
HNSW).  The dimension is fixed because pgvector indexes require it; every
embedding model the project recommends for Chinese text emits 1024.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from loguru import logger
from sqlalchemy import exists, select, text

from app.services.ai import strip_markup
from app.services.ai_client import AIError, LLMClient

#: How many profile vectors one book gets.
PROFILE_SEGMENTS = 4
#: Characters of chapter text kept per segment.
SEGMENT_CHARS = 900
#: Chapters read per book (evenly spread); fewer chapters are split instead.
SAMPLE_CHAPTERS = 12
#: Must match the ``vector(1024)`` column in migration 0040.
EMBEDDING_DIMS = 1024


def metadata_line(book: Any) -> str:
    """The short header prefixed to every segment."""
    parts = [f"《{getattr(book, 'title', '') or ''}》"]
    author = getattr(book, "author_name", "") or ""
    if author:
        parts.append(f"作者：{author}")
    tags = [str(tag) for tag in (getattr(book, "tag_names", None) or []) if str(tag).strip()][:8]
    if tags:
        parts.append("标签：" + "、".join(tags))
    categories = [
        str(name)
        for name in (getattr(book, "category_names", None) or [])
        if str(name).strip()
    ][:4]
    if categories:
        parts.append("分类：" + "、".join(categories))
    description = str(getattr(book, "description", "") or "").strip()
    if description:
        parts.append("简介：" + description[:160])
    return "｜".join(parts)


def pick_chapters(chapters: Sequence[Any], count: int = PROFILE_SEGMENTS) -> list[Any]:
    """Evenly spread ``count`` chapters across the book."""
    items = list(chapters)
    if len(items) <= count:
        return items
    step = (len(items) - 1) / (count - 1)
    picked: list[Any] = []
    for index in range(count):
        chapter = items[round(index * step)]
        if chapter not in picked:
            picked.append(chapter)
    return picked


def segment_texts(book: Any, chapters: Sequence[Any], read) -> list[str]:
    """Build the ``PROFILE_SEGMENTS`` texts for one book.

    ``read`` is a callable taking a ``content_path`` and returning the stored
    chapter text (it is called from a worker thread by the caller).
    """
    if not chapters:
        return []
    sampled = pick_chapters(chapters)
    bodies: list[str] = []
    for chapter in sampled:
        path = getattr(chapter, "content_path", None)
        if not path:
            continue
        try:
            raw = read(path)
        except Exception as exc:  # noqa: BLE001 - one unreadable chapter is not fatal
            logger.debug("book profile: cannot read {}: {}", path, exc)
            continue
        cleaned = strip_markup(raw)
        if cleaned:
            bodies.append(cleaned)
    if not bodies:
        return []

    if len(bodies) >= PROFILE_SEGMENTS:
        excerpts = [body[:SEGMENT_CHARS] for body in bodies[:PROFILE_SEGMENTS]]
    else:
        # A one- or two-chapter book still deserves several vectors: slice the
        # whole text instead of spending everything on one excerpt.
        joined = "\n".join(bodies)
        size = max(200, min(SEGMENT_CHARS, len(joined) // PROFILE_SEGMENTS))
        excerpts = [
            joined[start:start + size]
            for start in range(0, len(joined), size)
        ][:PROFILE_SEGMENTS]
    header = metadata_line(book)
    return [f"{header}\n\n{excerpt.strip()}" for excerpt in excerpts if excerpt.strip()]


def vector_literal(values: Sequence[float]) -> str:
    """pgvector's text input: ``[0.1,0.2,...]``."""
    return "[" + ",".join(f"{float(value):.7f}" for value in values) + "]"


def parse_vector_literal(raw: str) -> list[float]:
    body = str(raw or "").strip().strip("[]")
    if not body:
        return []
    return [float(piece) for piece in body.split(",") if piece.strip()]


async def index_book(db: Any, book: Any, client: LLMClient, *, read) -> int:
    """(Re)build one book's profile rows; returns how many were written."""
    from app.models import Chapter

    chapters = list(
        await db.scalars(
            select(Chapter)
            .where(Chapter.book_id == book.id)
            .order_by(Chapter.chapter_number)
        )
    )
    texts = segment_texts(book, chapters, read)
    if not texts:
        return 0
    vectors = await client.embed(texts, batch_size=max(1, len(texts)))
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMS:
            raise AIError(
                f"向量模型返回了 {len(vector)} 维，书库画像索引要求 {EMBEDDING_DIMS} 维。"
                "请改用 1024 维的向量模型（如 BAAI/bge-m3、text-embedding-v3、embedding-3）。"
            )
    await db.execute(
        text("DELETE FROM book_profiles WHERE book_id = :book_id"),
        {"book_id": str(book.id)},
    )
    await insert_profiles(db, book.id, texts, vectors, model=client.cfg.effective_embedding_model)
    return len(texts)


async def insert_profiles(db: Any, book_id: str, texts: Sequence[str],
                          vectors: Sequence[Sequence[float]], *, model: str) -> None:
    """Write profile rows through raw SQL so no pgvector Python driver is needed."""
    if not texts:
        return
    rows = [
        {
            "book_id": str(book_id),
            "segment": index,
            "excerpt": text[:SEGMENT_CHARS],
            "embedding": vector_literal(vector),
            "model": model or "",
        }
        for index, (text, vector) in enumerate(zip(texts, vectors))
    ]
    await db.execute(
        text(
            "INSERT INTO book_profiles (book_id, segment, excerpt, embedding, model) "
            "VALUES (:book_id, :segment, :excerpt, CAST(:embedding AS vector), :model) "
            "ON CONFLICT (book_id, segment) DO UPDATE SET "
            "excerpt = EXCLUDED.excerpt, embedding = EXCLUDED.embedding, "
            "model = EXCLUDED.model, updated_at = now()"
        ),
        rows,
    )


async def similar_books(db: Any, vector: Sequence[float], *, limit: int = 20,
                        exclude_book_id: str | None = None) -> list[dict[str, Any]]:
    """Nearest profile segments across the whole library, best first.

    Returns individual segments; the caller groups by book (a book can match
    with more than one segment, and the best one is what gets shown).
    """
    if not vector:
        return []
    # Over-fetch: invisible/R18-filtered books are dropped after the fetch, and
    # several segments of one book collapse into one result.
    fetch = max(40, int(limit) * 8)
    rows = (
        await db.execute(
            text(
                "SELECT book_id, segment, excerpt, model, "
                "       1 - (embedding <=> CAST(:query AS vector)) AS similarity "
                "FROM book_profiles "
                "ORDER BY embedding <=> CAST(:query AS vector) "
                "LIMIT :fetch"
            ),
            {"query": vector_literal(vector), "fetch": fetch},
        )
    ).all()
    results = []
    for book_id, segment, excerpt, model, similarity in rows:
        if exclude_book_id and book_id == exclude_book_id:
            continue
        results.append({
            "book_id": book_id,
            "segment": int(segment),
            "excerpt": excerpt or "",
            "model": model or "",
            "similarity": round(float(similarity or 0.0), 4),
        })
    return results


async def book_vector(db: Any, book_id: str) -> list[float] | None:
    """The book's average profile vector (used for "find books like this one")."""
    rows = (
        await db.execute(
            text(
                "SELECT embedding::text FROM book_profiles "
                "WHERE book_id = :book_id ORDER BY segment"
            ),
            {"book_id": str(book_id)},
        )
    ).all()
    vectors = [parse_vector_literal(row[0]) for row in rows if row[0]]
    vectors = [vec for vec in vectors if len(vec) == EMBEDDING_DIMS]
    if not vectors:
        return None
    return [sum(column) / len(vectors) for column in zip(*vectors)]


async def stats(db: Any) -> dict[str, Any]:
    """How many books/vectors exist and how much space they take."""
    try:
        totals = (
            await db.execute(
                text(
                    "SELECT count(*) AS vectors, count(DISTINCT book_id) AS books "
                    "FROM book_profiles"
                )
            )
        ).one()
    except Exception:  # pragma: no cover - table missing before the migration
        return {"books": 0, "vectors": 0, "db_bytes": 0}
    vectors = int(totals.vectors or 0)
    books = int(totals.books or 0)
    try:
        size = int(
            await db.scalar(text("SELECT pg_total_relation_size('book_profiles')"))
            or 0
        )
    except Exception:  # pragma: no cover
        size = 0
    return {"books": books, "vectors": vectors, "db_bytes": size}


def candidate_query(*, force: bool, limit: int):
    """SQL for books that still need a profile."""
    from app.models import Book, BookProfile, Chapter

    query = (
        select(Book)
        .where(Book.hidden == False)  # noqa: E712
        .where(
            exists().where(
                Chapter.book_id == Book.id,
                Chapter.content_path.is_not(None),
            )
        )
        .order_by(Book.updated_at.desc())
        .limit(limit)
    )
    if not force:
        query = query.where(~exists().where(BookProfile.book_id == Book.id))
    return query


__all__ = [
    "EMBEDDING_DIMS",
    "PROFILE_SEGMENTS",
    "SEGMENT_CHARS",
    "book_vector",
    "candidate_query",
    "index_book",
    "insert_profiles",
    "metadata_line",
    "parse_vector_literal",
    "pick_chapters",
    "segment_texts",
    "similar_books",
    "stats",
    "vector_literal",
]
