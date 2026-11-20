"""Reject chapter bodies that are actually someone else's cover wall.

Two sources render a "related works" grid inside the chapter container.  The
crawler saved that grid as the chapter body, so the book holds nothing but the
cover images of OTHER books (reported 2026-10-21: 《隐形的主人》 and friends).

The signal is content, not chapter count (the library has ~30 000 legitimate
one-chapter short stories): a body that is almost entirely images, whose ``alt``
text names *other* books in the library.  The book's own title never counts, so
a cover page of the work itself is kept.
"""
from __future__ import annotations

import re
import asyncio
import hashlib
from html import unescape
from typing import Iterable

IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
ALT_RE = re.compile(r"""alt\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")

#: Below this many visible characters the body is "images only".
MIN_TEXT_CHARS = 40
#: How many *other* book titles must appear as alt text before it is a wall.
MIN_MATCHED_TITLES = 2
#: A wall needs at least this many images (a cover page has one).
MIN_IMAGES = 2


def normalize_title(text: str) -> str:
    """Fold a title for comparison: no spaces, no punctuation, lower case."""
    value = str(text or "").strip().lower()
    value = re.sub(r"[\s\u3000]+", "", value)
    return re.sub(r"[，。！？、；：""''（）【】《》〈〉,.!?;:'\"()\\[\\]<>~～—-]+", "", value)


def visible_text(content: str) -> str:
    """The chapter body with every tag removed, whitespace folded."""
    text = TAG_RE.sub("", str(content or ""))
    return re.sub(r"\s+", "", text)


def cover_wall_labels(content: str) -> list[str]:
    """Extract candidate titles before paying for a library-wide lookup."""
    images = IMAGE_RE.findall(content)
    if len(images) < MIN_IMAGES:
        return []
    labels = []
    for image in images:
        match = ALT_RE.search(image)
        if match and match.group(1).strip():
            labels.append(match.group(1).strip())
    if len(set(labels)) < MIN_MATCHED_TITLES or len(visible_text(content)) >= MIN_TEXT_CHARS:
        return []
    return labels


def match_cover_titles(labels: list[str], known: set[str], book_title: str) -> list[str]:
    own = normalize_title(book_title)
    matched: dict[str, str] = {}
    for label in labels:
        alt = normalize_title(label)
        if not alt:
            continue
        if alt in known:
            hit = alt
        elif own and own in alt:
            continue
        else:
            hit = max((title for title in known if len(title) >= 4 and title in alt),
                      key=lambda title: (len(title), title), default="")
        if hit and hit != own:
            matched.setdefault(hit, label)
    return list(matched.values()) if len(matched) >= MIN_MATCHED_TITLES else []


def cover_wall_matches(content: str, other_titles: Iterable[str], book_title: str = "") -> list[str]:
    labels = cover_wall_labels(content)
    if not labels:
        return []
    return match_cover_titles(labels, {normalize_title(title) for title in other_titles if title}, book_title)


_SYNOPSIS_LABEL = r"(?:作品|内容|內容|故事|小说|小說|书籍|書籍)?(?:简介|簡介)"
_CARD = re.compile(
    r"^(?:(?:书名|書名|作品名)\s*[:：]\s*([^\n]{1,160})|《([^》\n]{1,120})》)"
    r"\s*\n\s*(?:作者|著者)\s*[:：]\s*[^\n]{1,80}\s*\n\s*"
    + _SYNOPSIS_LABEL + r"\s*[:：]\s*(.+)$", re.S,
)
_INTRO_CHAPTER = re.compile(r"(?:简介|簡介|文案|书单|書單|推书|推書|新书推荐|新書推薦|作品推荐|作品推薦)")


def _synopsis_text(value: str) -> str:
    value = unescape(TAG_RE.sub(" ", value))
    value = re.sub(r"^\s*" + _SYNOPSIS_LABEL + r"\s*[:：]", "", value)
    return re.sub(r"\s+", "", value).casefold()


def _synopsis_digest(value: str) -> bytes | None:
    text = _synopsis_text(value)
    if len(text) < 48:
        return None
    return hashlib.sha256(text.encode("utf-8")).digest()


class SynopsisCatalog:
    """A task-local snapshot shared by books from the same discovery task."""

    def __init__(self):
        self._scopes: dict[str | None, dict[bytes, str]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def remember(entries: dict[bytes, str], title: str, description: str | None) -> None:
        if not description:
            return
        key = _synopsis_digest(description)
        if key is not None:
            title_key = normalize_title(title)
            if entries.setdefault(key, title_key) != title_key:
                entries[key] = ""  # boilerplate shared by unrelated works

    async def for_owner(self, db, owner_id: str | None) -> dict[bytes, str]:
        from sqlalchemy import or_, select
        from app.models import Book

        async with self._lock:
            if owner_id not in self._scopes:
                query = select(Book.title, Book.description).where(
                    Book.hidden.is_(False), Book.description.is_not(None),
                    or_(Book.owner_id.is_(None), Book.owner_id == owner_id),
                )
                entries: dict[bytes, str] = {}
                rows = await db.stream(query.execution_options(yield_per=512))
                try:
                    async for title, description in rows:
                        self.remember(entries, title, description)
                finally:
                    await rows.close()
                self._scopes[owner_id] = entries
            return self._scopes[owner_id]


def foreign_synopsis(content: str, book_title: str, chapter_title: str,
                     catalog: dict[bytes, str]) -> bool:
    """Require a complete foreign synopsis or an explicitly labelled book card."""
    if _INTRO_CHAPTER.search(chapter_title or ""):
        return False
    text = content.strip()
    if text.startswith("#"):
        heading, sep, rest = text.partition("\n")
        if sep and heading.lstrip("# ").strip() == (chapter_title or "").strip():
            text = rest.strip()
    title = normalize_title(book_title)
    card = _CARD.fullmatch(text)
    if card and title:
        foreign = normalize_title(card[1] or card[2])
        blurb = card[3].strip()
        if foreign and foreign != title and "\n" not in blurb and len(_synopsis_text(blurb)) >= 48:
            return True
    if not catalog:
        return False
    key = _synopsis_digest(text)
    matched_title = catalog.get(key) if key else None
    return bool(matched_title and title and matched_title != title)
