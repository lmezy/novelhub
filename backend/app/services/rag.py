"""RAG service -- chunking, embedding, and semantic search for novels.

Uses the configured AI provider for embedding generation.
Stores embeddings as JSONB in PostgreSQL for zero-infra vector search.
"""

from __future__ import annotations

import math
import os
from typing import Optional
from uuid import uuid4

import httpx
from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Book, Chapter, ChapterEmbedding
from app.services.storage import BookStorage


class RAGService:
    """Retrieval-Augmented Generation for novel content.

    Pipeline: chunk -> embed -> store -> search -> augment
    """

    # Approximate token splitting (Chinese chars ~1.5 tokens, English words ~1.3 tokens)
    CHUNK_SIZE = 500   # target tokens per chunk
    CHUNK_OVERLAP = 100  # overlap between chunks
    MAX_CHUNKS_PER_BOOK = 2000

    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = BookStorage()
        self._embedding_model = os.getenv("AI_EMBEDDING_MODEL", "text-embedding-3-small")
        self._api_key = os.getenv("AI_API_KEY", "")
        self._base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")

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

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._embedding_model,
            "input": texts,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url}/embeddings",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]

    # -- Indexing --

    async def index_book(self, book_id: str) -> dict:
        """Index all chapters of a book for RAG search.

        Deletes existing embeddings first, then re-indexes.
        """
        book = await self.db.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book not found: {book_id}")

        # Delete existing embeddings
        await self.db.execute(
            delete(ChapterEmbedding).where(ChapterEmbedding.book_id == book_id)
        )
        await self.db.flush()

        # Load chapters
        result = await self.db.scalars(
            select(Chapter)
            .where(Chapter.book_id == book_id)
            .order_by(Chapter.chapter_number)
        )
        chapters = list(result)

        total_chunks = 0
        batch_texts = []
        batch_metas = []

        for chapter in chapters:
            try:
                content = self.storage.read_chapter(chapter.content_path)
            except Exception:
                logger.warning("Cannot read chapter {} content", chapter.id)
                continue

            # Remove markdown header
            if content.startswith("# "):
                content = content.split("\n", 1)[-1] if "\n" in content else ""

            chunks = self.chunk_text(content)
            for idx, chunk in enumerate(chunks):
                batch_texts.append(chunk)
                batch_metas.append({
                    "chapter_id": chapter.id,
                    "chunk_index": idx,
                    "chapter_number": chapter.chapter_number,
                    "title": chapter.title or "",
                })

            # Process in batches of 20 to avoid huge API calls
            while len(batch_texts) >= 20:
                await self._store_batch(book_id, batch_texts[:20], batch_metas[:20])
                total_chunks += 20
                batch_texts = batch_texts[20:]
                batch_metas = batch_metas[20:]

        # Store remaining
        if batch_texts:
            await self._store_batch(book_id, batch_texts, batch_metas)
            total_chunks += len(batch_texts)

        await self.db.commit()
        logger.info("Indexed book {}: {} chapters -> {} chunks", book_id, len(chapters), total_chunks)
        return {"book_id": book_id, "chapters": len(chapters), "chunks": total_chunks}

    async def _store_batch(self, book_id: str, texts: list[str],
                            metas: list[dict]) -> None:
        """Generate embeddings for a batch and store in DB."""
        embeddings = await self.generate_embeddings(texts)
        for text, meta, emb in zip(texts, metas, embeddings):
            chunk = ChapterEmbedding(
                id=str(uuid4()),
                book_id=book_id,
                chapter_id=meta["chapter_id"],
                chunk_index=meta["chunk_index"],
                content=text[:2000],  # truncate stored content
                embedding=emb,
                token_count=len(text) // 2,  # rough estimate
            )
            self.db.add(chunk)
        await self.db.flush()

    # -- Search --

    async def search(self, book_id: str, query: str, top_k: int = 5) -> list[dict]:
        """Semantic search within a book's indexed chunks.

        Returns top_k most relevant chunks with similarity scores.
        """
        # Generate query embedding
        query_emb = (await self.generate_embeddings([query]))[0]

        # Load all embeddings for this book
        result = await self.db.scalars(
            select(ChapterEmbedding).where(ChapterEmbedding.book_id == book_id)
        )
        chunks = list(result)

        if not chunks:
            return []

        # Compute cosine similarity (pure Python, no numpy dependency)
        scored = []
        for chunk in chunks:
            emb = chunk.embedding
            if not emb:
                continue
            similarity = self._cosine_similarity(query_emb, emb)
            scored.append({
                "chapter_id": chunk.chapter_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "similarity": round(similarity, 4),
            })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        top = scored[:top_k]

        # Enrich with chapter info
        chapter_ids = list(set(c["chapter_id"] for c in top))
        chapter_map = {}
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

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors (pure Python)."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    async def delete_book_index(self, book_id: str) -> None:
        """Remove all embeddings for a book."""
        await self.db.execute(
            delete(ChapterEmbedding).where(ChapterEmbedding.book_id == book_id)
        )
        await self.db.commit()
        logger.info("Deleted RAG index for book {}", book_id)
