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


def cover_wall_matches(
    content: str,
    other_titles: Iterable[str],
    book_title: str = "",
) -> list[str]:
    """Titles of *other* books whose covers this "chapter" is made of."""
    body = str(content or "")
    images = IMAGE_RE.findall(body)
    if len(images) < MIN_IMAGES:
        return []
    if len(visible_text(body)) >= MIN_TEXT_CHARS:
        return []

    known = {normalize_title(title) for title in other_titles if title}
    own = normalize_title(book_title)
    matched: list[str] = []
    for tag in images:
        found = ALT_RE.search(tag)
        if not found:
            continue
        alt = normalize_title(found.group(1))
        if not alt or alt == own or alt not in known:
            continue
        if alt not in [normalize_title(item) for item in matched]:
            matched.append(found.group(1).strip())
    return matched if len(matched) >= MIN_MATCHED_TITLES else []
