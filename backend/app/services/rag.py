"""RAG service -- chunking, embedding, and semantic search for novels.

Embeddings are produced through the same :class:`~app.services.ai_client.LLMClient`
the chat uses, so the RAG index honours the provider/base URL/proxy configured in
the admin UI.  Previously this module read ``AI_BASE_URL`` straight from the
environment and defaulted to OpenAI, which meant a DeepSeek/Qwen/Ollama install
tried to post its texts to ``api.openai.com``.

Vectors are stored as JSONB in PostgreSQL (no extra infrastructure) and the
similarity search uses numpy when it is available.
"""

from __future__ import annotations

import math
from typing import Any, Sequence
from uuid import uuid4

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, Chapter, ChapterEmbedding
from app.services.ai_client import AIError, LLMClient
from app.services.ai_config import AIConfig, get_ai_config
from app.services.storage import BookStorage

try:  # numpy ships with the backend image; keep a pure-Python fallback.
    import numpy as _np
except Exception:  # pragma: no cover - defensive
    _np = None


class RAGService:
    """Retrieval-Augmented Generation for novel content.

    Pipeline: chunk -> embed -> store -> search -> augment
    """

    # Approximate token splitting (Chinese chars ~1.5 tokens, English words ~1.3 tokens)
    CHUNK_SIZE = 500     # target tokens per chunk
    CHUNK_OVERLAP = 100  # overlap between chunks
    MAX_CHUNKS_PER_BOOK = 2000
    EMBED_BATCH_SIZE = 16
    #: Stored chunk text is capped: the vector carries the meaning, the text is
    #: only shown back to the model as a citation snippet.
    MAX_CHUNK_CHARS = 2000

    def __init__(self, db: AsyncSession, config: AIConfig | None = None):
        self.db = db
        self.storage = BookStorage()
        self._config = config

    async def config(self) -> AIConfig:
        if self._config is None:
            self._config = await get_ai_config(self.db)
        return self._config

    async def _client(self) -> LLMClient:
        cfg = await self.config()
        if not cfg.embeddings_supported:
            raise AIError(
                "RAG 语义检索需要一个支持向量化的服务：请在「设置 → AI」里配置 "
                "Embedding 服务与向量模型（例如 OpenAI text-embedding-3-small、"
                "通义千问 text-embedding-v3、智谱 embedding-3、SiliconFlow BAAI/bge-m3、"
                "Ollama nomic-embed-text）。"
            )
        return LLMClient(cfg)

    # -- Chunking --

    def chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks of roughly CHUNK_SIZE tokens.

        Uses a simple character-based heuristic: ~1.5 chars per token for Chinese,
        ~4 chars per token for English.
        """
        if not text.strip():
            return []

        # Approximate: 1 token ~= 2 characters for mixed CN/EN text
        chars_per_chunk = self.CHUNK_SIZE * 2
        overlap_chars = self.CHUNK_OVERLAP * 2

        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len and len(chunks) < self.MAX_CHUNKS_PER_BOOK:
            end = min(start + chars_per_chunk, text_len)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start += chars_per_chunk - overlap_chars
            if start >= text_len:
                break

        return chunks

    # -- Embedding --

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of texts."""
        if not texts:
            return []
        client = await self._client()
        return await client.embed(texts, batch_size=self.EMBED_BATCH_SIZE)

    # -- Indexing --

    async def index_status(self, book_id: str) -> dict[str, Any]:
        """How much of a book is currently indexed."""
        chunks = int(await self.db.scalar(
            select(func.count()).select_from(ChapterEmbedding)
            .where(ChapterEmbedding.book_id == book_id)
        ) or 0)
        indexed_chapters = int(await self.db.scalar(
            select(func.count(func.distinct(ChapterEmbedding.chapter_id)))
            .where(ChapterEmbedding.book_id == book_id)
        ) or 0)
        total_chapters = int(await self.db.scalar(
            select(func.count()).select_from(Chapter)
            .where(Chapter.book_id == book_id)
        ) or 0)
        cfg = await self.config()
        return {
            "book_id": book_id,
            "indexed": chunks > 0,
            "chunks": chunks,
            "indexed_chapters": indexed_chapters,
            "total_chapters": total_chapters,
            "ready": chunks > 0 and indexed_chapters >= total_chapters,
            "embeddings_supported": cfg.embeddings_supported,
            "embedding_model": cfg.effective_embedding_model,
        }

    async def index_book(self, book_id: str, *,
                         force: bool = False,
                         chapter_start: int | None = None,
                         chapter_end: int | None = None,
                         max_chunks: int | None = None) -> dict:
        """Index (or re-index) a book's chapters for semantic search."""
        book = await self.db.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book not found: {book_id}")

        client = await self._client()

        query = select(Chapter).where(Chapter.book_id == book_id)
        if chapter_start is not None:
            query = query.where(Chapter.chapter_number >= chapter_start)
        if chapter_end is not None:
            query = query.where(Chapter.chapter_number <= chapter_end)
        query = query.order_by(Chapter.chapter_number)
        chapters = list(await self.db.scalars(query))

        if not chapters:
            raise ValueError("这本书没有可索引的章节。")

        if not force and chapter_start is None and chapter_end is None:
            status = await self.index_status(book_id)
            if status["ready"]:
                return {
                    "book_id": book_id,
                    "chapters": len(chapters),
                    "chunks": status["chunks"],
                    "skipped": True,
                    "reason": "已索引且章节数一致；需要强制重建时使用 force=true。",
                }

        # Re-indexing a range would leave the rest of the book pointing at
        # deleted chapters, so a partial index always replaces everything.
        await self.db.execute(
            delete(ChapterEmbedding).where(ChapterEmbedding.book_id == book_id)
        )
        await self.db.flush()

        limit_chunks = int(max_chunks or self.MAX_CHUNKS_PER_BOOK)
        total_chunks = 0
        indexed_chapters = 0
        batch_texts: list[str] = []
        batch_metas: list[dict[str, Any]] = []
        skipped = 0

        async def flush_batch() -> None:
            nonlocal total_chunks, batch_texts, batch_metas
            if not batch_texts:
                return
            await self._store_batch(book_id, batch_texts, batch_metas)
            total_chunks += len(batch_texts)
            batch_texts = []
            batch_metas = []

        for chapter in chapters:
            if total_chunks >= limit_chunks:
                skipped += 1
                continue
            try:
                content = self.storage.read_chapter(chapter.content_path)
            except Exception as exc:
                logger.warning("RAG: cannot read chapter {}: {}", chapter.id, exc)
                continue
            from app.services.ai import strip_markup

            content = strip_markup(content)
            if not content:
                continue
            chunks = self.chunk_text(content)
            if not chunks:
                continue
            indexed_chapters += 1
            for idx, chunk in enumerate(chunks):
                if total_chunks + len(batch_texts) >= limit_chunks:
                    break
                batch_texts.append(chunk)
                batch_metas.append({
                    "chapter_id": chapter.id,
                    "chunk_index": idx,
                    "chapter_number": chapter.chapter_number,
                    "title": chapter.title or "",
                })
            if len(batch_texts) >= self.EMBED_BATCH_SIZE:
                await flush_batch()

        await flush_batch()
        # ``_store_batch`` flushes; commit so a cancelled request keeps progress.
        await self.db.commit()

        cfg = await self.config()
        logger.info(
            "RAG indexed book {}: {} chapters -> {} chunks ({})",
            book_id, indexed_chapters, total_chunks, cfg.effective_embedding_model,
        )
        return {
            "book_id": book_id,
            "title": book.title,
            "chapters": len(chapters),
            "indexed_chapters": indexed_chapters,
            "chunks": total_chunks,
            "truncated": skipped > 0 or total_chunks >= limit_chunks,
            "model": cfg.effective_embedding_model,
            "skipped": False,
        }

    async def _store_batch(self, book_id: str, texts: list[str],
                           metas: list[dict[str, Any]]) -> None:
        """Generate embeddings for a batch and store in DB."""
        client = await self._client()
        embeddings = await client.embed(texts, batch_size=self.EMBED_BATCH_SIZE)
        for text, meta, emb in zip(texts, metas, embeddings):
            chunk = ChapterEmbedding(
                id=str(uuid4()),
                book_id=book_id,
                chapter_id=meta["chapter_id"],
                chunk_index=meta["chunk_index"],
                content=text[:self.MAX_CHUNK_CHARS],
                embedding=emb,
                token_count=len(text) // 2,  # rough estimate
            )
            self.db.add(chunk)
        await self.db.flush()

    # -- Search --

    async def search(self, book_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Semantic search within a book's indexed chunks."""
        if not str(query or "").strip():
            raise ValueError("检索内容不能为空。")

        # Load the stored chunks first: an un-indexed book must not cost an
        # embedding round-trip (the reader asks on every question).
        rows = list(await self.db.scalars(
            select(ChapterEmbedding).where(ChapterEmbedding.book_id == book_id)
        ))
        if not rows:
            return []

        client = await self._client()
        vectors = await client.embed([query], batch_size=1)
        if not vectors:
            return []
        query_emb = vectors[0]

        scored = self._rank(query_emb, rows)
        top = scored[:max(1, int(top_k))]
        if not top:
            return []

        chapter_ids = list({item["chapter_id"] for item in top if item["chapter_id"]})
        chapter_map: dict[str, dict[str, Any]] = {}
        if chapter_ids:
            result = await self.db.scalars(
                select(Chapter).where(Chapter.id.in_(chapter_ids))
            )
            for ch in result:
                chapter_map[ch.id] = {
                    "chapter_number": ch.chapter_number,
                    "title": ch.title or "",
                }

        for item in top:
            ch_info = chapter_map.get(item["chapter_id"], {})
            item["chapter_number"] = ch_info.get("chapter_number", 0)
            item["chapter_title"] = ch_info.get("title", "")

        return top

    def _rank(self, query_emb: Sequence[float], rows: Sequence[ChapterEmbedding]) -> list[dict[str, Any]]:
        """Cosine similarity for every stored chunk, best first."""
        valid = [(row, row.embedding) for row in rows
                 if isinstance(row.embedding, list) and row.embedding]
        if not valid:
            return []

        if _np is not None:
            try:
                matrix = _np.asarray([vec for _, vec in valid], dtype=_np.float32)
                query = _np.asarray(query_emb, dtype=_np.float32)
                if matrix.ndim == 2 and matrix.shape[1] == query.shape[0]:
                    norms = _np.linalg.norm(matrix, axis=1)
                    qnorm = float(_np.linalg.norm(query))
                    denom = norms * qnorm
                    sims = _np.divide(
                        matrix @ query, denom,
                        out=_np.zeros_like(denom),
                        where=denom > 0,
                    )
                    order = _np.argsort(-sims)[:50]
                    return [
                        {
                            "chapter_id": valid[int(i)][0].chapter_id,
                            "chunk_index": valid[int(i)][0].chunk_index,
                            "content": valid[int(i)][0].content,
                            "similarity": round(float(sims[int(i)]), 4),
                        }
                        for i in order
                    ]
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("RAG: numpy ranking failed ({}), using pure Python", exc)

        scored = []
        for row, emb in valid:
            similarity = self._cosine_similarity(query_emb, emb)
            scored.append({
                "chapter_id": row.chapter_id,
                "chunk_index": row.chunk_index,
                "content": row.content,
                "similarity": round(similarity, 4),
            })
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored

    @staticmethod
    def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
        """Compute cosine similarity between two vectors (pure Python)."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # -- Housekeeping -------------------------------------------------------

    async def list_indexed_books(self, limit: int = 50) -> list[dict[str, Any]]:
        """Books that currently have vectors, newest index first."""
        rows = await self.db.execute(
            select(
                ChapterEmbedding.book_id,
                func.count().label("chunks"),
                func.count(func.distinct(ChapterEmbedding.chapter_id)).label("chapters"),
                func.max(ChapterEmbedding.created_at).label("indexed_at"),
            )
            .group_by(ChapterEmbedding.book_id)
            .order_by(func.max(ChapterEmbedding.created_at).desc())
            .limit(max(1, int(limit)))
        )
        entries = [
            {
                "book_id": row.book_id,
                "chunks": int(row.chunks or 0),
                "chapters": int(row.chapters or 0),
                "indexed_at": row.indexed_at.isoformat() if row.indexed_at else None,
            }
            for row in rows
        ]
        if not entries:
            return []

        titles: dict[str, str] = {}
        books = await self.db.scalars(
            select(Book).where(Book.id.in_([e["book_id"] for e in entries]))
        )
        for book in books:
            titles[book.id] = book.title
        for entry in entries:
            entry["title"] = titles.get(entry["book_id"], "")
        return entries

    async def delete_book_index(self, book_id: str) -> None:
        """Remove all embeddings for a book."""
        await self.db.execute(
            delete(ChapterEmbedding).where(ChapterEmbedding.book_id == book_id)
        )
        await self.db.commit()
        logger.info("Deleted RAG index for book {}", book_id)


__all__ = ["RAGService"]
