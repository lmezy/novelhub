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
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.routes import rag as rag_routes
from app.core.database import get_db
from app.main import app
from app.models import Book, BookTag, Chapter, ChapterEmbedding, Tag
from app.services import book_profiles, rag_jobs
from app.services.auth import get_current_user, require_admin
from app.services.rag import RAGService


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


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["unindexed", "book_ids"])
async def test_batch_accepts_zero_limits_via_admin_endpoint(scope, session_factory, monkeypatch, tmp_path):
    await _seed_books(
        session_factory,
        Book(id="first", title="第一本", kind="novel"),
        Book(id="second", title="第二本", kind="novel"),
        Book(id="comic", title="漫画", kind="comic"),
        Chapter(id="c1", book_id="first", chapter_number=1, content_path="/first"),
        Chapter(id="c2", book_id="second", chapter_number=1, content_path="/second"),
        Chapter(id="c3", book_id="comic", chapter_number=1, content_path="/comic"),
    )

    async def override_db():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(rag_jobs.settings, "STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(rag_jobs, "ensure_worker", lambda: False)
    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    monkeypatch.setitem(app.dependency_overrides, require_admin, lambda: None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/rag/batch?include_queue=false",
            json={"limit": 0, "max_chunks": 0, "scope": scope, "book_ids": ["second", "first", "comic"]},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["queued"] == payload["candidates"] == 2
    assert "queue" not in payload["state"]
    queue = rag_jobs.read_state()["queue"]
    assert {item["book_id"] for item in queue} == {"first", "second"}
    assert all(item["max_chunks"] == 0 for item in queue)
    if scope == "book_ids":
        assert [item["book_id"] for item in queue] == ["second", "first"]


@pytest.mark.asyncio
async def test_chapter_index_excludes_comics_and_images(session_factory, tmp_path):
    # Sync writes comic panels as HTML, not only as Markdown image references.
    gallery = tmp_path / "gallery.md"
    gallery.write_text('# 全话阅读\n\n<img src="/panel.webp">\n第 1 页', encoding="utf-8")
    illustrated = tmp_path / "novel.md"
    paragraph = "黄昏时，她沿着石阶回到窗前，悄悄把书放回原位。" * 10
    illustrated.write_text(
        f'# 第一章\n\n&lt;img src=&quot;/cover.webp&quot;&gt;\n\n{paragraph}\n\n![插图](/picture.jpg)',
        encoding="utf-8",
    )
    await _seed_books(
        session_factory,
        Book(id="comic", title="画册", kind="comic"),
        Book(id="novel", title="故事", kind="novel"),
        Chapter(id="gallery", book_id="novel", chapter_number=1, content_path=str(gallery)),
        Chapter(id="prose", book_id="novel", chapter_number=2, content_path=str(illustrated)),
    )

    client = SimpleNamespace(embed=AsyncMock(return_value=[[1.0, 0.0]]))
    config = SimpleNamespace(effective_embedding_model="test-model")
    async with session_factory() as session:
        service = RAGService(session, config=config)
        service._client = AsyncMock(return_value=client)
        assert (await service.index_book("comic", force=True))["skipped"]
        service._client.assert_not_awaited()
        result = await service.index_book("novel", force=True)
        assert result["indexed_chapters"] == 1
        client.embed.assert_awaited_once_with([paragraph], batch_size=service.EMBED_BATCH_SIZE)
        rows = (await session.scalars(select(ChapterEmbedding))).all()
        assert [(row.chapter_id, row.content) for row in rows] == [("prose", paragraph)]
        session.add(ChapterEmbedding(
            id="old-gallery", book_id="novel", chapter_id="gallery", chunk_index=0,
            content='&lt;img src="/panel.webp"&gt;', embedding=[1.0, 0.0],
        ))
        await session.commit()
        assert [hit["chapter_id"] for hit in await service.search("novel", "她回哪里？")] == ["prose"]
