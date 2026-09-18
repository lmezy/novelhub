"""Tell novels and comics apart so the library can page them separately.

NovelHub keeps both in ``books``, but they are read in completely different
ways: a novel chapter is prose, a comic chapter is an image gallery.  The
distinction is derived from the book source and the stored chapters -- no
site is special-cased, and the same rules work for a source imported later:

1. the book source declares itself as an image source (Legado
   ``bookSourceType == 2``);
2. the book's own tags/categories say 漫画 / 图集 / 写真 (or a variant);
3. the stored chapter body is image markup with no prose, the shape NovelHub
   writes for an album chapter.

Rule 3 is the safety net for the many comic sources that (incorrectly)
declare ``bookSourceType == 0``: their chapters are still galleries.

Kinds only ever move *up* to ``comic`` automatically.  A novel is never
re-labelled just because a source page was parsed oddly; the admin
re-classification endpoint is what resets a wrong value.
"""

import re
from typing import Iterable


KIND_NOVEL = "novel"
KIND_COMIC = "comic"
KINDS = (KIND_NOVEL, KIND_COMIC)

# Legado book source types: 0 text, 1 audio, 2 image, 3 file.
IMAGE_SOURCE_TYPE = "2"

# Normalised (lowercase, no separators) labels that mark a book as a comic.
COMIC_LABEL_KEYWORDS = (
    "漫画",
    "漫畫",
    "图集",
    "圖集",
    "画集",
    "畫集",
    "写真",
    "寫真",
    "comic",
    "comics",
    "manga",
    "webtoon",
    "doujinshi",
)

IMAGE_REF_RE = re.compile(
    r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*\bsrc\s*=",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^#.*(?:\r?\n|$)", re.MULTILINE)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

# A gallery page may carry a caption or a page number, so "no prose" cannot
# mean zero characters; it means "far too little text to be a chapter".
COMIC_TEXT_MAX_LENGTH = 120


def normalize_kind(value: str | None) -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in KINDS else KIND_NOVEL


def normalize_label(value: str | None) -> str:
    return re.sub(r"[\s\-_/、,，。.]+", "", str(value or "")).lower()


def is_comic_source(source) -> bool:
    """Whether a book source declares itself as an image/comic source."""
    if source is None:
        return False
    config = getattr(source, "config", None)
    if config is None and isinstance(source, dict):
        config = source
    if not isinstance(config, dict):
        return False
    return str(config.get("bookSourceType", "") or "").strip() == IMAGE_SOURCE_TYPE


def is_comic_label(value: str | None) -> bool:
    label = normalize_label(value)
    if not label:
        return False
    return any(keyword in label for keyword in COMIC_LABEL_KEYWORDS)


def is_comic_labels(values: Iterable[str] | None) -> bool:
    return any(is_comic_label(value) for value in values or [])


def is_comic_content(content: str | None) -> bool:
    """Whether a stored chapter is an image gallery instead of prose.

    ``content`` is what ``BookStorage`` wrote for a chapter: markdown with an
    optional ``#`` title plus the body.  A chapter counts as a comic page when
    it references images and the text left over after stripping the markup is
    just a caption (or nothing at all).
    """
    if not content or not IMAGE_REF_RE.search(content):
        return False
    body = HEADING_RE.sub("", content)
    body = MARKDOWN_IMAGE_RE.sub("", body)
    body = HTML_TAG_RE.sub(" ", body)
    body = WHITESPACE_RE.sub(" ", body).strip()
    return len(body) < COMIC_TEXT_MAX_LENGTH


def resolve_kind(
    *,
    source=None,
    labels: Iterable[str] | None = None,
    content: str | None = None,
    current: str | None = None,
) -> str:
    """Return the kind a book should have, never downgrading automatically."""
    if is_comic_source(source) or is_comic_labels(labels) or is_comic_content(content):
        return KIND_COMIC
    return normalize_kind(current)


def classify_book(
    book,
    *,
    source=None,
    content: str | None = None,
    current: str | None = ...,
) -> str:
    """Kind for a stored book row, using its tags, categories and source.

    ``current`` defaults to the book's stored kind (upgrade-only).  Pass
    ``None`` to re-derive the value from scratch.
    """
    if current is ...:
        current = getattr(book, "kind", None)
    labels = [*getattr(book, "tag_names", []), *getattr(book, "category_names", [])]
    return resolve_kind(
        source=source,
        labels=labels,
        content=content,
        current=current,
    )


RECLASSIFY_CHUNK_SIZE = 500


async def reclassify_books(
    db,
    *,
    scan_content: bool = True,
    source_id: str | None = None,
    force: bool = False,
    chunk_size: int = RECLASSIFY_CHUNK_SIZE,
) -> dict:
    """Recompute ``Book.kind`` for books already in the library.

    The cheap rules (source type, tags, categories) come first; ``scan_content``
    additionally reads the first stored chapter of every remaining book so
    galleries from sources that declare themselves as text are caught too.
    ``force`` ignores the stored value and re-derives it from scratch, which is
    what turns a wrong ``comic`` back into a ``novel``; without it the rules
    only promote, exactly like a sync does.  Returns counters so an admin can
    see what changed.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import Book, Chapter, Source
    from app.services.storage import BookStorage

    storage = BookStorage()
    result = {
        "books": 0,
        "comics": 0,
        "novels": 0,
        "updated": 0,
        "scanned": 0,
        "content_comics": 0,
        "scan_content": bool(scan_content),
        "force": bool(force),
        "source_id": source_id,
        # Ids whose kind changed, so the caller can refresh just those documents
        # in the search index instead of re-indexing the whole library.
        # ``/api/books/reclassify`` pops this before serializing the response.
        "changed_book_ids": [],
    }

    sources = {
        source.id: source
        for source in (await db.scalars(select(Source))).all()
    }

    last_id = ""
    while True:
        query = (
            select(Book)
            .options(selectinload(Book.tags), selectinload(Book.categories))
            .where(Book.id > last_id)
            .order_by(Book.id)
            .limit(chunk_size)
        )
        if source_id:
            query = query.where(Book.source_id == source_id)
        books = list((await db.scalars(query)).unique().all())
        if not books:
            break
        last_id = books[-1].id
        books_by_id = {book.id: book for book in books}

        pending: dict[str, str] = {}
        for book in books:
            kind = classify_book(
                book,
                source=sources.get(book.source_id),
                current=None if force else book.kind,
            )
            if kind != KIND_COMIC and scan_content:
                pending[book.id] = kind
            if kind != normalize_kind(book.kind):
                book.kind = kind
                result["updated"] += 1
                result["changed_book_ids"].append(book.id)

        if pending:
            chapter_rows = (
                await db.execute(
                    select(Chapter.book_id, Chapter.content_path)
                    .where(Chapter.book_id.in_(list(pending)))
                    .order_by(Chapter.book_id, Chapter.chapter_number)
                )
            ).all()
            first_path: dict[str, str] = {}
            for book_id, content_path in chapter_rows:
                if content_path and book_id not in first_path:
                    first_path[book_id] = content_path
            for book_id, path in first_path.items():
                result["scanned"] += 1
                try:
                    content = storage.read_chapter(path)
                except Exception:
                    continue
                if not is_comic_content(content):
                    continue
                result["content_comics"] += 1
                book = books_by_id.get(book_id)
                if book is not None and normalize_kind(book.kind) != KIND_COMIC:
                    book.kind = KIND_COMIC
                    result["updated"] += 1
                    result["changed_book_ids"].append(book.id)

        for book in books:
            if normalize_kind(book.kind) == KIND_COMIC:
                result["comics"] += 1
            else:
                result["novels"] += 1
        result["books"] += len(books)
        await db.commit()

    return result
