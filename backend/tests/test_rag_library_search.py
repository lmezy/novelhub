"""Regressions for the joined-eager ``Book.tags`` load.

``Book.tags`` is ``lazy="joined"``, so SQLAlchemy refuses to consume the Result
of ``select(Book)`` unless ``.unique()`` was called:

    InvalidRequestError: The unique() method must be invoked on this Result...

``/rag/library/search`` only reached that query when at least one profile
segment matched -- an empty library returned ``{"items": []}`` happily -- so the
endpoint 500ed for exactly the users who had built a library profile.  The same
omission sat in ``RAGService.list_indexed_books`` (the admin "indexed books"
list).

These tests drive a real (in-memory) ORM session, because the regression lives
in how the Result is consumed: a mocked session accepts any code shape and
proves nothing.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import rag as rag_routes
from app.core.database import get_db
from app.main import app
from app.models import Book, BookTag, ChapterEmbedding, Tag
from app.models.base import Base
from app.services import book_profiles
from app.services.auth import get_current_user
from app.services.rag import RAGService


@pytest_asyncio.fixture
async def session_factory():
    """SQLite session factory over the real metadata.

    SQLite cannot compile ``JSONB``, and several tables (``users``,
    ``sources``, ``chapter_embeddings`` ...) carry it.  The columns are swapped
    to the generic ``JSON`` type for the duration of the test and restored
    afterwards, so nothing leaks into the rest of the suite.
    """
    swapped = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, JSONB):
                swapped.append((column, column.type))
                column.type = JSON()
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        for column, original in swapped:
            column.type = original


async def _seed_books(Session, *books) -> None:
    async with Session() as session:
        session.add_all(list(books))
        await session.commit()


def _patch_library_search(monkeypatch, session_factory, hits, *, vector=None):
    class _Cfg:
        embeddings_supported = True
        effective_embedding_model = "bge-m3"

    async def _fake_config(db):
        return _Cfg()

    async def _fake_book_vector(db, book_id):
        return vector if vector is not None else [0.01] * 4

    async def _fake_similar(db, vector, *, limit=20, exclude_book_id=None):
        return hits

    async def _override_db():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(rag_routes, "get_ai_config", _fake_config)
    monkeypatch.setattr(book_profiles, "book_vector", _fake_book_vector)
    monkeypatch.setattr(book_profiles, "similar_books", _fake_similar)

    user = SimpleNamespace(
        id="u1", role="user", non_r18_enabled=True, r18_enabled=False
    )
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user


async def _post_library_search(book_id: str, top_k: int = 12):
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/rag/library/search",
                json={"book_id": book_id, "top_k": top_k},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_library_search_returns_hits_for_books_with_tag_collections(
    monkeypatch, session_factory
):
    await _seed_books(
        session_factory,
        Tag(id="t1", name="都市"),
        Tag(id="t2", name="悬疑"),
        Book(
            id="b1",
            title="深巷有雨",
            is_public=True,
            is_r18=False,
            tags=[BookTag(book_id="b1", tag_id="t1"), BookTag(book_id="b1", tag_id="t2")],
        ),
    )
    _patch_library_search(
        monkeypatch,
        session_factory,
        [
            {"book_id": "b1", "segment": 0, "similarity": 0.91, "excerpt": "雨夜的巷口"},
            {"book_id": "b1", "segment": 2, "similarity": 0.72, "excerpt": "旧伞下的约定"},
        ],
    )

    response = await _post_library_search("b1")

    assert response.status_code == 200, response.text
    payload = response.json()
    # Two matching segments of one book collapse into a single card.
    assert [item["book_id"] for item in payload["items"]] == ["b1"]
    assert payload["items"][0]["title"] == "深巷有雨"
    assert payload["items"][0]["similarity"] == 0.91
    assert payload["items"][0]["excerpt"] == "雨夜的巷口"


@pytest.mark.asyncio
async def test_library_search_drops_hidden_hits(monkeypatch, session_factory):
    """The same query must still filter invisible books out of the results."""
    await _seed_books(
        session_factory,
        Book(id="b2", title="隐藏的书", is_public=True, is_r18=False, hidden=True),
    )
    _patch_library_search(
        monkeypatch,
        session_factory,
        [{"book_id": "b2", "segment": 0, "similarity": 0.9, "excerpt": "x"}],
    )

    response = await _post_library_search("b2")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@pytest.mark.asyncio
async def test_list_indexed_books_titles_come_from_a_column_query(session_factory):
    """The admin list only needs id/title; it must not load ``Book`` entities."""
    async with session_factory() as session:
        session.add(
            Book(
                id="b3",
                title="带标签的书",
                is_public=True,
                is_r18=False,
                tags=[BookTag(book_id="b3", tag_id="t3")],
            )
        )
        session.add(Tag(id="t3", name="都市"))
        session.add_all(
            [
                ChapterEmbedding(id="e1", book_id="b3", chapter_id="c1", chunk_index=0),
                ChapterEmbedding(id="e2", book_id="b3", chapter_id="c2", chunk_index=0),
            ]
        )
        await session.commit()

    async with session_factory() as session:
        entries = await RAGService(session).list_indexed_books()

    assert [entry["book_id"] for entry in entries] == ["b3"]
    assert entries[0]["title"] == "带标签的书"
    assert entries[0]["chunks"] == 2
    assert entries[0]["chapters"] == 2
