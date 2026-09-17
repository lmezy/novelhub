"""AI reading assistant.

The service used to be a thin wrapper that always fed the *first* N chapters of
a book into a single prompt, so asking about the chapter you are actually
reading answered from chapter 1, the token counters were hard-coded to ``0``,
and a Claude configuration could never work.  This version:

* loads the configuration from the database (:mod:`app.services.ai_config`),
  editable in the admin UI and falling back to ``AI_*`` environment variables;
* assembles context around the reader's *current* chapter, or from the RAG
  index when the question is a semantic one (RAG hits come back with the
  chapter they came from, so answers can be cited);
* summarises long books with a bounded map-reduce pass instead of silently
  reading the first 30 chapters;
* reports real token usage;
* streams answers for the reader-side panel;
* supports the "selected text" actions (解释 / 翻译 / 润色).

It is a *consumer* of the archive only: it never crawls, parses or writes
chapters.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, Chapter
from app.services.ai_client import AIError, LLMClient
from app.services.ai_config import AIConfig, get_ai_config, not_configured_reason
from app.services.storage import BookStorage

#: Characters of a single chapter kept when building a prompt.
MAX_CHAPTER_CHARS = 4000
#: Chapters summarised in one map step.
SUMMARY_BATCH_SIZE = 6
#: Hard ceiling for a summarise request (a 3000-chapter book would otherwise
#: take hours and a lot of tokens).
SUMMARY_MAX_CHAPTERS = 60
#: Ceiling for character/timeline extraction over a whole book.
ANALYSIS_MAX_CHAPTERS = 40
#: How many trailing chat turns are replayed to the model.
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_CHARS = 2000

IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\([^)\s]+(?:\s+[\"'][^\"']*[\"'])?\)")

SYSTEM_CHAT = (
    "你是一位博学的小说阅读助手，帮助读者理解他正在读的这本小说。\n"
    "规则：\n"
    "1. 只依据下面给出的原文片段和书籍信息回答，不要编造原文里没有的情节。\n"
    "2. 如果原文片段不足以回答，直接说明“给出的片段里没有提到”，并建议读者提供更多章节。\n"
    "3. 引用原文时标出章节号，例如「（第 12 章）」。\n"
    "4. 回答使用与提问相同的语言，简洁、有条理，不要复述整段原文。"
)

SYSTEM_SUMMARY_BATCH = (
    "你是文学分析助手。下面是一部小说的若干连续章节正文，"
    "请提炼情节推进、人物变化与关键信息，用要点列出，不要评论文笔，不要编造。"
)

SYSTEM_SUMMARY_FINAL = (
    "你是文学分析助手。下面是同一部小说各段落的分段摘要，"
    "请合并成一份完整、有条理的摘要，覆盖主线剧情、主要人物与主题。"
)

SYSTEM_CHARACTERS = (
    "你是文学分析助手。根据给定的原文片段识别主要人物。"
    "只输出一个 JSON 数组，不要输出任何解释文字，格式："
    '[{"name":"人物名","role":"主角/反派/配角","description":"简要介绍",'
    '"relationships":"与其他主要人物的关系","first_chapter":章节号或null}]'
)

SYSTEM_TIMELINE = (
    "你是文学分析助手。根据给定的原文片段整理主要事件的时间线。"
    "只输出一个 JSON 数组，不要输出任何解释文字，格式："
    '[{"chapter":章节号或null,"event":"事件","significance":"对剧情的影响"}]'
)

SYSTEM_TRANSFORM = {
    "explain": (
        "你是一位耐心的中文文学讲师。下面是从小说里摘出的一段文字，"
        "请解释它的含义：生僻词、典故、比喻、人物关系、上下文暗示。分点说明，不要复述原文。"
    ),
    "translate": (
        "你是专业文学翻译。把下面的小说片段翻译成{target}，"
        "保持原文的语气与分段，人名/地名保留原文并在括号里给出译名。只输出译文。"
    ),
    "polish": (
        "你是资深文字编辑。在完全不改变情节、人物与信息量的前提下，"
        "润色下面这段小说文字（通顺、去重复、修标点）。只输出润色后的正文。"
    ),
    "continue": (
        "你是小说续写助手。根据下面这段原文的风格、人称与叙事节奏续写一小段（200 字以内），"
        "不要引入原文没有的人物或设定，不要总结。只输出续写内容。"
    ),
    "custom": "你是小说阅读助手。按用户的要求处理下面的小说片段。",
}


def _trim(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（已截断）"


def strip_markup(text: str) -> str:
    """Drop markdown image markers and normalise blank lines for prompts."""
    cleaned = IMAGE_MARKDOWN_RE.sub("", str(text or ""))
    if cleaned.startswith("# "):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else ""
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def parse_json_list(raw: str) -> tuple[list[dict[str, Any]], bool]:
    """Best-effort JSON array extraction from a model reply.

    Models routinely wrap JSON in ```` ```json ```` fences or add a sentence
    before it; the old implementation fell back to a single "Parsing failed"
    row whenever ``json.loads`` raised, which threw away a perfectly good
    answer.  Returns ``(items, parsed)``.
    """
    text = str(raw or "").strip()
    if not text:
        return [], False

    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S | re.I)
    if fence:
        text = fence.group(1).strip()

    candidates = [text]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append("[" + text[start:end + 1] + "]")

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            for key in ("items", "characters", "events", "data", "result", "list"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
            if items:
                return items, True

    # Last resort: no JSON at all -- hand the prose back so the UI can still
    # show something useful instead of a fake row.
    return [{"name": "", "description": text}], False


@dataclass
class ContextBundle:
    """Prompt context plus where it came from."""

    text: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "none"
    chapters: int = 0
    truncated: bool = False


class AIService:
    """AI-powered reading assistant (chat / summary / characters / timeline)."""

    def __init__(self, db: AsyncSession, config: AIConfig | None = None):
        self.db = db
        self.storage = BookStorage()
        self._config = config

    # -- configuration ------------------------------------------------------

    async def config(self) -> AIConfig:
        if self._config is None:
            self._config = await get_ai_config(self.db)
        return self._config

    async def _client(self) -> LLMClient:
        cfg = await self.config()
        if not cfg.configured:
            raise AIError(not_configured_reason(cfg))
        return LLMClient(cfg)

    @staticmethod
    def _not_configured_reason(cfg: AIConfig) -> str:
        return not_configured_reason(cfg)

    async def status(self) -> dict[str, Any]:
        """Configuration + index summary for the front-end."""
        cfg = await self.config()
        payload = cfg.public_dict()
        payload["available"] = cfg.configured
        payload["reason"] = "" if cfg.configured else self._not_configured_reason(cfg)
        return payload

    # -- data loading -------------------------------------------------------

    async def _book(self, book_id: str) -> Book:
        book = await self.db.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book not found: {book_id}")
        return book

    async def _chapters(self, book_id: str, *,
                        chapter_start: int | None = None,
                        chapter_end: int | None = None,
                        limit: int | None = None) -> list[Chapter]:
        query = select(Chapter).where(Chapter.book_id == book_id)
        if chapter_start is not None:
            query = query.where(Chapter.chapter_number >= chapter_start)
        if chapter_end is not None:
            query = query.where(Chapter.chapter_number <= chapter_end)
        query = query.order_by(Chapter.chapter_number)
        if limit:
            query = query.limit(limit)
        return list(await self.db.scalars(query))

    def _read(self, chapter: Chapter) -> str:
        if not chapter.content_path:
            return ""
        try:
            return strip_markup(self.storage.read_chapter(chapter.content_path))
        except Exception as exc:
            logger.debug("AI: cannot read chapter {}: {}", chapter.id, exc)
            return ""

    def _format(self, chapter: Chapter, text: str, *, limit: int = MAX_CHAPTER_CHARS) -> str:
        title = f" 第 {chapter.chapter_number} 章 {chapter.title or ''}".rstrip()
        body = _trim(text, limit) if limit else text
        if not body:
            body = "[本章正文为空或不可读]"
        return f"===== 第 {chapter.chapter_number} 章：{chapter.title or ''} =====\n{body}"

    @staticmethod
    def _source(chapter: Chapter) -> dict[str, Any]:
        return {
            "chapter_id": chapter.id,
            "chapter_number": chapter.chapter_number,
            "title": chapter.title or "",
        }

    async def _bundle_window(self, book_id: str, chapter_number: int | None,
                             span: int, budget: int) -> ContextBundle:
        """Chapters around the reader's current position."""
        span = max(1, min(int(span or 5), 20))
        if chapter_number is None:
            chapters = await self._chapters(book_id, limit=span)
            mode = "head"
        else:
            half = span // 2
            start = max(1, int(chapter_number) - half)
            chapters = await self._chapters(
                book_id, chapter_start=start,
                chapter_end=int(chapter_number) + max(half, span - half - 1),
                limit=span,
            )
            if not chapters:
                chapters = await self._chapters(
                    book_id, chapter_end=chapter_number, limit=span
                )
            mode = "window"

        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        used = 0
        truncated = False
        for chapter in chapters:
            text = self._read(chapter)
            block = self._format(chapter, text)
            if used + len(block) > budget and parts:
                truncated = True
                break
            parts.append(block)
            sources.append(self._source(chapter))
            used += len(block)
        return ContextBundle("\n\n".join(parts), sources, mode, len(sources), truncated)

    async def _bundle_sampled(self, book_id: str, *, max_chapters: int,
                              budget: int, mode: str = "sampled") -> ContextBundle:
        """Evenly sampled chapters across the whole book (analysis tasks)."""
        chapters = await self._chapters(book_id)
        if not chapters:
            return ContextBundle("", [], mode, 0, False)
        max_chapters = max(1, int(max_chapters))
        if len(chapters) > max_chapters:
            step = len(chapters) / max_chapters
            picked = [chapters[min(len(chapters) - 1, int(i * step))]
                      for i in range(max_chapters)]
            seen: set[str] = set()
            chapters = [c for c in picked if not (c.id in seen or seen.add(c.id))]
        per_chapter = max(400, budget // max(1, len(chapters)))
        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        for chapter in chapters:
            block = self._format(chapter, self._read(chapter),
                                 limit=min(MAX_CHAPTER_CHARS, per_chapter))
            parts.append(block)
            sources.append(self._source(chapter))
        return ContextBundle("\n\n".join(parts), sources, mode, len(sources), False)

    async def _bundle_rag(self, book_id: str, question: str, top_k: int) -> ContextBundle:
        """Semantic retrieval over the RAG index; empty when unavailable."""
        cfg = await self.config()
        if not cfg.rag_enabled or not question:
            return ContextBundle("", [], "none", 0, False)
        if not cfg.embeddings_supported:
            return ContextBundle("", [], "none", 0, False)
        try:
            from app.services.rag import RAGService

            hits = await RAGService(self.db, cfg).search(
                book_id=book_id, query=question, top_k=top_k
            )
        except AIError as exc:
            logger.warning("AI: RAG retrieval failed, falling back to chapter window: {}", exc)
            return ContextBundle("", [], "none", 0, False)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("AI: RAG retrieval error: {}", exc)
            return ContextBundle("", [], "none", 0, False)

        if not hits:
            return ContextBundle("", [], "none", 0, False)

        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in hits:
            chapter_id = str(hit.get("chapter_id") or "")
            title = hit.get("chapter_title") or ""
            number = hit.get("chapter_number")
            parts.append(
                f"----- 第 {number} 章 {title}（片段）-----\n{hit.get('content') or ''}"
            )
            if chapter_id and chapter_id not in seen:
                seen.add(chapter_id)
                sources.append({
                    "chapter_id": chapter_id,
                    "chapter_number": number,
                    "title": title,
                    "similarity": hit.get("similarity"),
                })
        return ContextBundle("\n\n".join(parts), sources, "rag", len(sources), False)

    async def build_context(self, book_id: str, *, question: str = "",
                            chapter_number: int | None = None,
                            span: int = 5, mode: str = "auto") -> ContextBundle:
        """Pick the best available context for a question.

        ``auto`` prefers the RAG index (so a question about something from
        chapter 400 still finds it) and falls back to a window around the
        reader's current chapter when the book is not indexed.
        """
        cfg = await self.config()
        budget = max(2000, int(cfg.context_chars))

        if mode == "rag":
            bundle = await self._bundle_rag(book_id, question, cfg.rag_top_k)
            if bundle.sources:
                return bundle
        elif mode == "auto":
            bundle = await self._bundle_rag(book_id, question, cfg.rag_top_k)
            if bundle.sources:
                return bundle

        return await self._bundle_window(book_id, chapter_number, span, budget)

    # -- chat ---------------------------------------------------------------

    @staticmethod
    def _history_messages(history: Sequence[dict[str, Any]] | None) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for item in (history or [])[-MAX_HISTORY_MESSAGES:]:
            if not isinstance(item, dict):
                continue
            role = "assistant" if str(item.get("role")) == "assistant" else "user"
            content = _trim(str(item.get("content") or ""), MAX_HISTORY_CHARS)
            if content:
                out.append({"role": role, "content": content})
        return out

    async def _chat_messages(self, book: Book, bundle: ContextBundle, message: str,
                             history: Sequence[dict[str, Any]] | None) -> list[dict[str, str]]:
        meta = [f"书名：{book.title}"]
        if book.author_name:
            meta.append(f"作者：{book.author_name}")
        if book.description:
            meta.append(f"简介：{_trim(book.description, 600)}")
        if bundle.mode == "rag":
            meta.append("以下是与问题最相关的原文片段（按相关度排序）。")
        elif bundle.mode == "window":
            meta.append("以下是读者当前阅读位置附近的原文。")
        elif bundle.mode == "head":
            meta.append("以下是本书开头的原文（未提供阅读位置）。")
        else:
            meta.append("本书暂时没有可用的原文片段，请基于书名与简介回答，并说明这一点。")

        user = "\n".join(meta)
        if bundle.text:
            user += "\n\n" + bundle.text
        user += f"\n\n===== 读者的问题 =====\n{message}"

        messages = [{"role": "system", "content": SYSTEM_CHAT}]
        messages.extend(self._history_messages(history))
        messages.append({"role": "user", "content": user})
        return messages

    async def prepare_chat(self, book_id: str, message: str, *,
                           chapter_number: int | None = None,
                           context_chapters: int = 5,
                           history: Sequence[dict[str, Any]] | None = None,
                           mode: str = "auto") -> tuple[LLMClient, list[dict[str, str]], ContextBundle]:
        """Everything needed to start a chat stream (or a one-shot answer)."""
        book = await self._book(book_id)
        client = await self._client()
        bundle = await self.build_context(
            book_id, question=message, chapter_number=chapter_number,
            span=context_chapters, mode=mode,
        )
        messages = await self._chat_messages(book, bundle, message, history)
        return client, messages, bundle

    async def chat(self, book_id: str, message: str, *,
                   chapter_number: int | None = None,
                   context_chapters: int = 5,
                   history: Sequence[dict[str, Any]] | None = None,
                   mode: str = "auto") -> dict:
        """Answer a question about a book using context-aware AI."""
        client, messages, bundle = await self.prepare_chat(
            book_id, message, chapter_number=chapter_number,
            context_chapters=context_chapters, history=history, mode=mode,
        )
        cfg = await self.config()
        result = await client.chat(messages)
        if not result.text:
            raise AIError("AI 返回了空内容，请重试或换一个模型。")
        return {
            "answer": result.text,
            "model": result.model or cfg.effective_model,
            "provider": cfg.provider,
            "tokens_used": result.tokens_used,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "context_mode": bundle.mode,
            "sources": bundle.sources,
        }

    async def stream_chat(self, book_id: str, message: str, *,
                          chapter_number: int | None = None,
                          context_chapters: int = 5,
                          history: Sequence[dict[str, Any]] | None = None,
                          mode: str = "auto") -> AsyncIterator[dict[str, Any]]:
        """Yield ``sources`` / ``delta`` / ``done`` / ``error`` events."""
        try:
            client, messages, bundle = await self.prepare_chat(
                book_id, message, chapter_number=chapter_number,
                context_chapters=context_chapters, history=history, mode=mode,
            )
        except AIError as exc:
            yield {"type": "error", "message": str(exc)}
            return

        cfg = await self.config()
        yield {
            "type": "sources",
            "context_mode": bundle.mode,
            "sources": bundle.sources,
            "provider": cfg.provider,
            "model": cfg.effective_model,
        }

        collected: list[str] = []
        try:
            async for delta in client.stream(messages):
                collected.append(delta)
                yield {"type": "delta", "text": delta}
        except AIError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("AI stream failed")
            yield {"type": "error", "message": f"AI 流式响应失败：{exc}"}
            return

        yield {
            "type": "done",
            "model": cfg.effective_model,
            "answer": "".join(collected),
        }

    # -- summary ------------------------------------------------------------

    def _summary_range(self, chapters: list[Chapter], max_chapters: int) -> tuple[list[Chapter], bool]:
        max_chapters = max(1, min(int(max_chapters or SUMMARY_MAX_CHAPTERS),
                                  SUMMARY_MAX_CHAPTERS * 4))
        if len(chapters) <= max_chapters:
            return chapters, False
        step = len(chapters) / max_chapters
        picked = [chapters[min(len(chapters) - 1, int(i * step))] for i in range(max_chapters)]
        seen: set[str] = set()
        return [c for c in picked if not (c.id in seen or seen.add(c.id))], True

    async def summarize(self, book_id: str, *,
                        chapter_start: int | None = None,
                        chapter_end: int | None = None,
                        max_chapters: int = SUMMARY_MAX_CHAPTERS,
                        style: str = "detailed") -> dict:
        """Summarise a chapter range with a bounded map-reduce pass."""
        book = await self._book(book_id)
        client = await self._client()
        cfg = await self.config()

        chapters = await self._chapters(
            book_id, chapter_start=chapter_start, chapter_end=chapter_end
        )
        if not chapters:
            raise ValueError("这本书在这个章节范围里没有可用的章节。")
        selected, sampled = self._summary_range(chapters, max_chapters)
        if style == "brief":
            batch_prompt_tail = "用不超过 5 条要点概括，每条不超过 40 字。"
        else:
            batch_prompt_tail = "用要点列出关键情节与人物变化，每条不超过 80 字。"

        prompt_tokens = 0
        completion_tokens = 0
        partials: list[str] = []
        for index in range(0, len(selected), SUMMARY_BATCH_SIZE):
            group = selected[index:index + SUMMARY_BATCH_SIZE]
            body = "\n\n".join(self._format(ch, self._read(ch)) for ch in group)
            first, last = group[0].chapter_number, group[-1].chapter_number
            user = (
                f"书名：{book.title}\n"
                f"章节范围：第 {first} - {last} 章\n\n"
                f"{body}\n\n请{batch_prompt_tail}"
            )
            result = await client.chat(
                [{"role": "system", "content": SYSTEM_SUMMARY_BATCH},
                 {"role": "user", "content": user}],
                max_tokens=900,
            )
            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            if result.text:
                partials.append(f"【第 {first}-{last} 章】\n{result.text}")

        if not partials:
            raise AIError("AI 返回了空摘要，请重试或换一个模型。")

        if len(partials) == 1:
            summary = partials[0].split("\n", 1)[-1].strip()
        else:
            user = (
                f"书名：{book.title}\n"
                f"作者：{book.author_name or '未知'}\n"
                f"简介：{_trim(book.description or '', 600)}\n\n"
                + "\n\n".join(partials)
                + "\n\n请给出整本书的完整摘要。"
            )
            result = await client.chat(
                [{"role": "system", "content": SYSTEM_SUMMARY_FINAL},
                 {"role": "user", "content": user}],
                max_tokens=2200,
            )
            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            summary = result.text or "\n\n".join(partials)

        return {
            "summary": summary.strip(),
            "chapters_covered": len(selected),
            "total_chapters": len(chapters),
            "chapter_start": selected[0].chapter_number,
            "chapter_end": selected[-1].chapter_number,
            "sampled": sampled,
            "model": cfg.effective_model,
            "provider": cfg.provider,
            "tokens_used": prompt_tokens + completion_tokens,
        }

    # -- analysis -----------------------------------------------------------

    async def analyze_characters(self, book_id: str, *,
                                 chapter_start: int | None = None,
                                 chapter_end: int | None = None) -> dict:
        book = await self._book(book_id)
        client = await self._client()
        cfg = await self.config()
        bundle = await self._bundle_sampled(
            book_id, max_chapters=ANALYSIS_MAX_CHAPTERS,
            budget=cfg.context_chars, mode="sampled",
        )
        if not bundle.text:
            raise ValueError("这本书还没有可读取的正文，无法识别人物。")

        user = (
            f"书名：{book.title}\n"
            f"作者：{book.author_name or '未知'}\n"
            f"简介：{_trim(book.description or '', 600)}\n\n"
            f"{bundle.text}\n\n请识别主要人物。"
        )
        result = await client.chat(
            [{"role": "system", "content": SYSTEM_CHARACTERS},
             {"role": "user", "content": user}],
            max_tokens=3000,
        )
        characters, parsed = parse_json_list(result.text)
        return {
            "characters": characters,
            "parsed": parsed,
            "raw": "" if parsed else result.text[:2000],
            "model": result.model or cfg.effective_model,
            "provider": cfg.provider,
            "tokens_used": result.tokens_used,
            "chapters_scanned": bundle.chapters,
        }

    async def extract_timeline(self, book_id: str, *,
                               chapter_start: int | None = None,
                               chapter_end: int | None = None) -> dict:
        book = await self._book(book_id)
        client = await self._client()
        cfg = await self.config()
        bundle = await self._bundle_sampled(
            book_id, max_chapters=ANALYSIS_MAX_CHAPTERS,
            budget=cfg.context_chars, mode="sampled",
        )
        if not bundle.text:
            raise ValueError("这本书还没有可读取的正文，无法整理时间线。")

        user = (
            f"书名：{book.title}\n"
            f"作者：{book.author_name or '未知'}\n\n"
            f"{bundle.text}\n\n请整理主要事件的时间线。"
        )
        result = await client.chat(
            [{"role": "system", "content": SYSTEM_TIMELINE},
             {"role": "user", "content": user}],
            max_tokens=3000,
        )
        events, parsed = parse_json_list(result.text)
        return {
            "events": events,
            "parsed": parsed,
            "raw": "" if parsed else result.text[:2000],
            "model": result.model or cfg.effective_model,
            "provider": cfg.provider,
            "tokens_used": result.tokens_used,
            "chapters_scanned": bundle.chapters,
        }

    # -- selected-text actions ---------------------------------------------

    async def prepare_transform(self, text: str, action: str, *,
                                book_id: str | None = None,
                                chapter_number: int | None = None,
                                instruction: str = "",
                                target_language: str = "中文",
                                ) -> tuple[LLMClient, list[dict[str, str]], AIConfig]:
        """Validate a selected-text action and build its prompt."""
        action = (action or "explain").strip().lower()
        if action not in SYSTEM_TRANSFORM:
            raise ValueError(f"不支持的 AI 操作：{action}")
        passage = str(text or "").strip()
        if not passage:
            raise ValueError("请先选中一段文字。")
        passage = _trim(passage, 8000)

        client = await self._client()
        cfg = await self.config()

        system = SYSTEM_TRANSFORM[action].format(target=target_language or "中文")
        user_parts: list[str] = []
        if book_id:
            try:
                book = await self._book(book_id)
                user_parts.append(f"书名：{book.title}")
                if book.author_name:
                    user_parts.append(f"作者：{book.author_name}")
            except ValueError:
                pass
        if chapter_number:
            user_parts.append(f"当前章节：第 {chapter_number} 章")
        if action == "custom" and instruction:
            user_parts.append(f"用户要求：{instruction}")
        user_parts.append("原文片段：\n" + passage)
        if action in ("explain", "custom") and not instruction:
            user_parts.append("请按系统提示处理这段文字。")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]
        return client, messages, cfg

    async def transform(self, text: str, action: str, *,
                        book_id: str | None = None,
                        chapter_number: int | None = None,
                        instruction: str = "",
                        target_language: str = "中文") -> dict:
        """解释 / 翻译 / 润色 a passage selected in the reader."""
        client, messages, cfg = await self.prepare_transform(
            text, action, book_id=book_id, chapter_number=chapter_number,
            instruction=instruction, target_language=target_language,
        )
        result = await client.chat(
            messages, max_tokens=min(max(cfg.max_tokens, 512), 2048)
        )
        if not result.text:
            raise AIError("AI 返回了空内容，请重试。")
        return {
            "action": (action or "explain").strip().lower(),
            "result": result.text,
            "model": result.model or cfg.effective_model,
            "provider": cfg.provider,
            "tokens_used": result.tokens_used,
        }

    async def stream_transform(self, text: str, action: str, *,
                               book_id: str | None = None,
                               chapter_number: int | None = None,
                               instruction: str = "",
                               target_language: str = "中文") -> AsyncIterator[dict[str, Any]]:
        """Streamed selected-text action (reader front-end)."""
        try:
            client, messages, cfg = await self.prepare_transform(
                text, action, book_id=book_id, chapter_number=chapter_number,
                instruction=instruction, target_language=target_language,
            )
        except AIError as exc:
            yield {"type": "error", "message": str(exc)}
            return

        yield {"type": "start", "action": action, "model": cfg.effective_model,
               "provider": cfg.provider}
        collected: list[str] = []
        try:
            async for delta in client.stream(
                messages, max_tokens=min(max(cfg.max_tokens, 512), 2048)
            ):
                collected.append(delta)
                yield {"type": "delta", "text": delta}
        except AIError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("AI transform stream failed")
            yield {"type": "error", "message": f"AI 流式响应失败：{exc}"}
            return
        yield {"type": "done", "result": "".join(collected), "model": cfg.effective_model}


__all__ = ["AIService", "ContextBundle", "parse_json_list", "strip_markup"]
