"""AI service -- multi-provider support for chat/summary/characters/timeline.

Supported providers: OpenAI, Claude, Qwen, Ollama (local), Hermes.
All providers use API keys from environment variables.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Book, Chapter


class AIService:
    """AI-powered reading assistant.

    Configure via environment variables:
        AI_PROVIDER=openai|claude|qwen|ollama|hermes
        AI_API_KEY=...
        AI_BASE_URL=...  (optional, for custom endpoints)
        AI_MODEL=...     (optional, provider default used if not set)
    """

    PROVIDER_DEFAULTS = {
        "openai": {"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"},
        "claude": {"model": "claude-3-haiku-20240307", "base_url": "https://api.anthropic.com/v1"},
        "qwen": {"model": "qwen-turbo", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        "ollama": {"model": "llama3", "base_url": "http://localhost:11434/v1"},
        "hermes": {"model": "hermes-3", "base_url": "http://localhost:8080/v1"},
    }

    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = os.getenv("AI_PROVIDER", "openai").lower()
        self.api_key = os.getenv("AI_API_KEY", "")
        self.base_url = os.getenv("AI_BASE_URL", self.PROVIDER_DEFAULTS[self.provider]["base_url"])
        self.model = os.getenv("AI_MODEL", self.PROVIDER_DEFAULTS[self.provider]["model"])

    async def _call_llm(self, system_prompt: str, user_prompt: str,
                        max_tokens: int = 2000) -> str:
        """Call the configured LLM provider with system + user prompts."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _get_book_context(self, book_id: str,
                                 chapter_start: Optional[int] = None,
                                 chapter_end: Optional[int] = None,
                                 context_chapters: int = 5) -> tuple[Book, str]:
        """Fetch book metadata and concatenated chapter content."""
        book = await self.db.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book not found: {book_id}")

        query = select(Chapter).where(Chapter.book_id == book_id)
        if chapter_start is not None:
            query = query.where(Chapter.chapter_number >= chapter_start)
        if chapter_end is not None:
            query = query.where(Chapter.chapter_number <= chapter_end)
        query = query.order_by(Chapter.chapter_number)

        if context_chapters > 0:
            query = query.limit(context_chapters)

        result = await self.db.scalars(query)
        chapters = list(result)

        content_parts = []
        for ch in chapters:
            content_parts.append(f"--- Chapter {ch.chapter_number}: {ch.title or ''} ---\n{ch.content_path}")

        # Read actual content from files
        from app.services.storage import BookStorage
        storage = BookStorage()
        full_text_parts = []
        for ch in chapters:
            try:
                text = storage.read_chapter(ch.content_path)
                full_text_parts.append(f"=== Chapter {ch.chapter_number}: {ch.title or ''} ===\n\n{text[:3000]}")
            except Exception:
                full_text_parts.append(f"=== Chapter {ch.chapter_number}: {ch.title or ''} ===\n\n[content not available]")

        return book, "\n\n".join(full_text_parts)

    async def chat(self, book_id: str, message: str,
                    context_chapters: int = 5) -> dict:
        """Answer a question about a book using context-aware AI."""
        book, context = await self._get_book_context(
            book_id, context_chapters=context_chapters
        )

        system = (
            "You are a knowledgeable literary assistant helping a reader understand a novel. "
            "Answer questions based on the provided book content. "
            "If the answer is not found in the context, say so honestly. "
            "Keep answers concise and insightful."
        )

        user = (
            f"Book: {book.title}\n"
            f"Description: {book.description or 'N/A'}\n\n"
            f"Context (relevant chapters):\n{context}\n\n"
            f"Question: {message}"
        )

        answer = await self._call_llm(system, user)
        return {"answer": answer.strip(), "model": self.model, "tokens_used": 0}

    async def summarize(self, book_id: str,
                         chapter_start: Optional[int] = None,
                         chapter_end: Optional[int] = None) -> dict:
        """Generate a summary of a book or chapter range."""
        limit = 30 if chapter_start is None else (chapter_end or chapter_start) - (chapter_start or 1) + 1
        book, context = await self._get_book_context(
            book_id, chapter_start=chapter_start,
            chapter_end=chapter_end, context_chapters=min(limit, 30)
        )

        system = (
            "You are a literary analyst. Provide a clear, structured summary "
            "of the novel content provided. Cover key plot points, character "
            "developments, and themes. Be concise but thorough."
        )

        user = (
            f"Book: {book.title}\n"
            f"Description: {book.description or 'N/A'}\n\n"
            f"Content to summarize:\n{context}"
        )

        summary = await self._call_llm(system, user, max_tokens=4000)
        return {"summary": summary.strip(), "chapters_covered": 0, "model": self.model}

    async def analyze_characters(self, book_id: str) -> dict:
        """Identify and describe key characters in a book."""
        book, context = await self._get_book_context(book_id, context_chapters=50)

        system = (
            "You are a literary analyst. Identify the major characters from "
            "the provided novel content. For each character, provide: name, "
            "role (protagonist/antagonist/supporting), brief description, "
            "and key relationships. Output as JSON array."
        )

        user = (
            f"Book: {book.title}\n"
            f"Description: {book.description or 'N/A'}\n\n"
            f"Content:\n{context[:15000]}"
        )

        result = await self._call_llm(system, user, max_tokens=3000)

        # Try to parse JSON, fallback to raw text
        try:
            import json
            characters = json.loads(result)
        except json.JSONDecodeError:
            characters = [{"name": "Parsing failed", "description": result[:500]}]

        return {"characters": characters if isinstance(characters, list) else [characters],
                "model": self.model}

    async def extract_timeline(self, book_id: str) -> dict:
        """Extract a chronological timeline of events from a book."""
        book, context = await self._get_book_context(book_id, context_chapters=50)

        system = (
            "You are a literary analyst. Extract the chronological timeline "
            "of major events from the provided novel content. For each event, "
            "provide: chapter reference, event description, and significance. "
            "Output as JSON array of objects with keys: chapter, event, significance."
        )

        user = (
            f"Book: {book.title}\n"
            f"Description: {book.description or 'N/A'}\n\n"
            f"Content:\n{context[:15000]}"
        )

        result = await self._call_llm(system, user, max_tokens=3000)

        try:
            import json
            events = json.loads(result)
        except json.JSONDecodeError:
            events = [{"chapter": "N/A", "event": "Parsing failed",
                        "significance": result[:500]}]

        return {"events": events if isinstance(events, list) else [events],
                "model": self.model}
