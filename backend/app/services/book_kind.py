"""Tell novels and comics apart so the library can page them separately.

NovelHub keeps both in ``books``, but they are read in completely different
ways: a novel chapter is prose, a comic chapter is an image gallery.  The
distinction is derived from the book source, the stored chapters and the
book's own labels -- no site is special-cased, and the same rules work for a
source imported later.

Three kinds of evidence, weakest first:

1. **labels** -- the book's tags/categories say 漫画 / 图集 / 写真.  Weak: an
   auto-category can be inferred from a *title* that merely mentions 漫画
   (《在漫畫店被輪姦》 is prose), and some text sources tag everything 漫画.
2. **source** -- the book source declares itself as an image source (Legado
   ``bookSourceType == 2``).
3. **content** -- the stored chapter bodies.  Strong, because it is what the
   reader actually gets; it is also the only evidence that can *clear* a wrong
   label.

Content is decided by a **vote over several chapters**, never by one chapter.
A lot of novels carry a placeholder/cover image as their first chapter, and
the old "first chapter only" rule turned every one of those into a comic
(2026-10-22: 20 books; 《好色人妻》 had 1 image in chapter 1 and prose in the
other 17).  A single placeholder must not outweigh the rest of the book.

Votes are combined as follows:

* no chapters stored  -> no content evidence, labels/source decide.  A comic
  that has not synced its chapters yet keeps its kind instead of being flipped
  to novel for want of something to read.
* chapters stored     -> content wins, in both directions.  This is what lets
  「重新识别」 rescue the novels that a stray 漫画 tag locked as comics.

Kinds still only ever move *up* to ``comic`` during a routine sync; the admin
re-classification endpoint is what re-derives a wrong value.
"""

import asyncio
import re
from typing import Iterable, Sequence


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

#: How many chapters a re-classification reads per book.  A 150-chapter novel
#: does not need 150 reads to prove it is prose, and the spread means a
#: placeholder in chapter 1 stays a minority of one.  Decisive books stop
#: earlier than this (see ``_undecided_after``).
KIND_SCAN_CHAPTERS = 6
#: Share of sampled chapters that must look like galleries for the book to be
#: a comic.  Strictly greater than half, so ties resolve to "novel": a book we
#: cannot agree on is prose, which is the kind that never hides chapters from
#: the reader behind an image viewer.
KIND_COMIC_QUORUM = 0.5
#: A gallery page holds a whole album; a cover/placeholder holds one image and
#: nothing else.  A chapter that matches is *ambiguous* and abstains rather
#: than voting for either side (see ``is_ambiguous_chapter``).
PLACEHOLDER_MAX_IMAGES = 1

#: Legado merges every page of an image source into one chapter and titles it
#: 全话阅读 ("read all").  A chapter carrying that title is an album even when
#: only one page made it through -- which is common, and is why "one image and
#: no words" alone cannot be the placeholder test.
GALLERY_TITLE_MARKERS = ("全话阅读", "全話閱讀")


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


def strip_chapter_text(content: str) -> str:
    """Chapter body with its title line, images and markup removed."""
    body = HEADING_RE.sub("", content or "")
    body = MARKDOWN_IMAGE_RE.sub("", body)
    body = HTML_TAG_RE.sub(" ", body)
    return WHITESPACE_RE.sub(" ", body).strip()


def is_gallery_chapter(content: str | None) -> bool:
    """Whether a chapter is a Legado image-source album (titled 全话阅读).

    That title is how image sources are told apart from a novel chapter that
    happens to be one picture: a single-page album is still an album, while a
    novel's "chapter" is usually a cover or a stray site icon.
    """
    if not content:
        return False
    head = content[:40].replace("\n", "")
    return any(marker in head for marker in GALLERY_TITLE_MARKERS)


def is_ambiguous_chapter(content: str | None) -> bool:
    """Whether a chapter is one lone image with no words and no album marker.

    Two different things look like that, and nothing on the page tells them
    apart:

    * a novel's placeholder -- a cover, or a stray site icon such as the
      「搜索」 button, which is what 《好色人妻》 and 《禁锢的爱情》 store as
      chapter 1;
    * a one-page album from an image source that only got one picture through.

    Most of the library's comics are single-chapter books, so guessing "novel"
    here would demote them wholesale.  Such a chapter therefore **abstains**
    (see ``chapter_comic_vote``) instead of voting, and the rest of the book
    decides.
    """
    if not content:
        return False
    if is_gallery_chapter(content):
        return False
    images = len(IMAGE_REF_RE.findall(content))
    if images > PLACEHOLDER_MAX_IMAGES:
        return False
    return images > 0 and not strip_chapter_text(content)


def is_comic_content(content: str | None) -> bool:
    """Whether a stored chapter is an image gallery instead of prose.

    ``content`` is what ``BookStorage`` wrote for a chapter: markdown with an
    optional ``#`` title plus the body.  A chapter counts as a comic page when
    it references images and the text left over after stripping the markup is
    just a caption (or nothing at all).
    """
    if not content or not IMAGE_REF_RE.search(content):
        return False
    return len(strip_chapter_text(content)) < COMIC_TEXT_MAX_LENGTH


def chapter_comic_vote(content: str | None) -> bool | None:
    """One chapter's vote: True gallery, False prose, ``None`` abstain.

    A chapter abstains when it is a single image with no words and no album
    marker -- equally likely a novel's placeholder and a one-page album.  A
    novel is still rescued because its other chapters vote prose, while a
    one-chapter album is left alone instead of being demoted on a guess.
    """
    if not is_comic_content(content):
        return False
    if is_ambiguous_chapter(content):
        return None
    return True


def content_verdict(votes: Sequence[bool]) -> bool | None:
    """Fold per-chapter votes into a verdict: True comic, False novel, None unknown.

    ``None`` means nothing decisive could be read -- no chapters at all, or
    nothing but ambiguous ones -- so the caller must fall back to labels and the
    stored value: a comic whose chapters have not synced yet must not be
    re-labelled as a novel just because there is nothing to read.

    Abstentions (``None`` votes) are dropped rather than counted as prose.
    """
    decided = [vote for vote in votes if vote is not None]
    if not decided:
        return None
    comic = sum(1 for vote in decided if vote)
    return comic > len(decided) * KIND_COMIC_QUORUM


#: How many chapters must vote "gallery" before a *sync* promotes the book to
#: comic.  Two, not one: a sync can only upgrade (never demote), so a wrong
#: promotion is permanent until someone re-classifies.
SYNC_MIN_COMIC_VOTES = 2


def should_promote_to_comic(votes: Sequence[bool]) -> bool:
    """Whether the chapters written so far justify calling the book a comic.

    One chapter is never enough.  Novel sources routinely prepend a cover or a
    placeholder image, and promoting on that single chapter used to turn the
    whole novel into a comic for good -- which is exactly the "第一章是占位图"
    case.  A lone chapter is left to the re-classification endpoint, which can
    also look at the rest of the book.
    """
    if not votes:
        return False
    comic = sum(1 for vote in votes if vote)
    if comic < SYNC_MIN_COMIC_VOTES and len(votes) < SYNC_MIN_COMIC_VOTES:
        return False
    return comic > len(votes) * KIND_COMIC_QUORUM


def resolve_kind(
    *,
    source=None,
    labels: Iterable[str] | None = None,
    content: str | None = None,
    content_votes: Sequence[bool] | None = None,
    current: str | None = None,
) -> str:
    """Return the kind a book should have.

    Content evidence wins over labels and over the stored value, in both
    directions: it is the only signal that can clear a wrong ``comic``.
    Without content evidence the cheaper rules decide, and the stored value
    survives.
    """
    votes = list(content_votes) if content_votes is not None else None
    if votes is None and content is not None:
        votes = [chapter_comic_vote(content)]
    if votes is not None:
        # Drop abstentions: an ambiguous chapter is not a vote for either side.
        votes = [vote for vote in votes if vote is not None]
    verdict = content_verdict(votes) if votes is not None else None

    if verdict is not None:
        return KIND_COMIC if verdict else KIND_NOVEL
    if is_comic_source(source) or is_comic_labels(labels):
        return KIND_COMIC
    return normalize_kind(current)


def classify_book(
    book,
    *,
    source=None,
    content: str | None = None,
    content_votes: Sequence[bool] | None = None,
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
        content_votes=content_votes,
        current=current,
    )


RECLASSIFY_CHUNK_SIZE = 500


def sample_chapter_indexes(total: int, limit: int = KIND_SCAN_CHAPTERS) -> list[int]:
    """Chapter positions to read for a book with ``total`` chapters.

    Always includes the first chapter (so a book whose *only* chapter is a
    gallery is still recognised) and spreads the rest over the whole book, so a
    placeholder at the front stays one vote out of many instead of the only
    vote that matters.
    """
    if total <= 0 or limit <= 0:
        return []
    if total <= limit:
        return list(range(total))
    step = total / float(limit)
    indexes = sorted({int(i * step) for i in range(limit)})
    if 0 not in indexes:
        indexes.insert(0, 0)
    return indexes


def _undecided_after(votes: list[bool], total: int) -> bool:
    """Whether more chapters could still change the verdict.

    Lets a decisive book stop early: a novel stops after a handful of prose
    chapters instead of reading all ``KIND_SCAN_CHAPTERS``, which is the
    difference between minutes and hours on a 46 000-book library.
    """
    comic = sum(1 for vote in votes if vote)
    remaining = total - len(votes)
    # Best case for "comic": every remaining chapter votes comic.
    best_comic = comic + remaining
    # Worst case for "comic": every remaining chapter votes prose.
    return (
        best_comic > total * KIND_COMIC_QUORUM
        and comic <= total * KIND_COMIC_QUORUM
    )


async def scan_book_votes(
    storage,
    paths: Sequence[str],
    limit: int = KIND_SCAN_CHAPTERS,
) -> tuple[list[bool], int]:
    """Read sampled chapters; return ``(votes, chapters_read)``.

    Abstentions are dropped from ``votes``, so ``paths`` can yield fewer votes
    than chapters read -- a book made only of ambiguous chapters has nothing
    to say.  ``chapters_read`` counts every chapter successfully read,
    abstentions included, so progress reporting reflects the real I/O.

    The reads go through ``asyncio.to_thread``: ``storage.read_chapter`` is
    blocking file I/O, and awaiting it inline would freeze the uvicorn worker
    running the scan -- with ``--workers 2`` that is half the API, progress
    polls included, for the whole run.
    """
    indexes = sample_chapter_indexes(len(paths), limit)
    votes: list[bool] = []
    reads = 0
    for index in indexes:
        try:
            content = await asyncio.to_thread(storage.read_chapter, paths[index])
        except Exception:
            continue
        reads += 1
        vote = chapter_comic_vote(content)
        if vote is not None:
            votes.append(vote)
        if not _undecided_after(votes, len(indexes)):
            break
    return votes, reads


async def reclassify_books(
    db,
    *,
    scan_content: bool = True,
    source_id: str | None = None,
    force: bool = False,
    chunk_size: int = RECLASSIFY_CHUNK_SIZE,
    progress=None,
    should_stop=None,
) -> dict:
    """Recompute ``Book.kind`` for books already in the library.

    The cheap rules (source type, tags, categories) come first; ``scan_content``
    additionally reads several stored chapters of every book so galleries from
    sources that declare themselves as text are caught too, **and** so novels
    locked as comics by a stray 漫画 tag -- or by a placeholder image as their
    first chapter -- are put back.

    ``force`` now only concerns books with *nothing* stored: with chapters on
    disk the verdict is the same either way, because content beats the stored
    value in both directions.  A book whose chapters never synced keeps its
    labels' verdict, so a comic that has not downloaded yet is never flipped to
    novel for want of something to read.

    Returns counters so an admin can see what changed.
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm import selectinload

    from app.models import Book, Chapter, Source
    from app.services.storage import BookStorage

    def publish(**update) -> None:
        if progress is not None:
            try:
                progress(update)
            except Exception:  # pragma: no cover - progress must never break a run
                pass

    storage = BookStorage()
    result = {
        "books": 0,
        "comics": 0,
        "novels": 0,
        "updated": 0,
        "scanned": 0,
        "chapters_read": 0,
        "content_comics": 0,
        "content_novels": 0,
        "label_only": 0,
        "no_chapters": 0,
        "scan_content": bool(scan_content),
        "force": bool(force),
        "source_id": source_id,
        "cancelled": False,
        # Ids whose kind changed, so the caller can refresh just those documents
        # in the search index instead of re-indexing the whole library.
        # ``/api/books/reclassify`` pops this before serializing the response.
        "changed_book_ids": [],
    }

    sources = {
        source.id: source
        for source in (await db.scalars(select(Source))).all()
    }

    count_query = select(func.count()).select_from(Book)
    if source_id:
        count_query = count_query.where(Book.source_id == source_id)
    total_books = int(await db.scalar(count_query) or 0)

    last_id = ""
    while True:
        if should_stop is not None and should_stop():
            result["cancelled"] = True
            break
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

        # Content scan.  Every book with stored chapters is scanned, including
        # the ones the cheap rules already call a comic: that is the only way a
        # stray 漫画 tag or a placeholder first chapter can be cleared.
        verdicts: dict[str, bool] = {}
        paths: dict[str, list[str]] = {}
        if scan_content:
            chapter_rows = (
                await db.execute(
                    select(Chapter.book_id, Chapter.content_path)
                    .where(Chapter.book_id.in_(list(books_by_id)))
                    .order_by(Chapter.book_id, Chapter.chapter_number)
                )
            ).all()
            for book_id, content_path in chapter_rows:
                if content_path:
                    paths.setdefault(book_id, []).append(content_path)

            for book_id, book_paths in paths.items():
                result["scanned"] += 1
                votes, reads = await scan_book_votes(storage, book_paths)
                result["chapters_read"] += reads
                verdict = content_verdict(votes)
                if verdict is None:
                    # Chapter rows exist but none could be read; no evidence.
                    continue
                verdicts[book_id] = verdict
                if verdict:
                    result["content_comics"] += 1
                else:
                    result["content_novels"] += 1
            result["no_chapters"] += sum(1 for book in books if not paths.get(book.id))

        for book in books:
            verdict = verdicts.get(book.id)
            if verdict is not None:
                # Content decides, in both directions.
                kind = KIND_COMIC if verdict else KIND_NOVEL
            elif is_comic_source(sources.get(book.source_id)) or is_comic_labels(
                [*book.tag_names, *book.category_names]
            ):
                # No chapter to read, but the source or the labels speak for it.
                kind = KIND_COMIC
                result["label_only"] += 1
            elif force and not paths.get(book.id):
                # Nothing at all was ever stored for this book, so the old
                # value is stale by definition rather than merely unproven.
                kind = KIND_NOVEL
            else:
                # Unreadable or unsynced chapters: keep what is stored instead
                # of re-labelling a comic whose chapters simply are not here.
                kind = normalize_kind(book.kind)
            if kind != normalize_kind(book.kind):
                book.kind = kind
                result["updated"] += 1
                result["changed_book_ids"].append(book.id)
            if normalize_kind(book.kind) == KIND_COMIC:
                result["comics"] += 1
            else:
                result["novels"] += 1
        result["books"] += len(books)
        await db.commit()
        publish(
            books_done=result["books"],
            books_total=total_books,
            updated=result["updated"],
            scanned=result["scanned"],
            phase="scanning",
        )

    return result
