"""Tests for novel/comic classification (``app.services.book_kind``)."""

import pytest
from types import SimpleNamespace

from app.services import book_kind as book_kind_module
from app.services.book_kind import (
    KIND_COMIC,
    KIND_NOVEL,
    classify_book,
    is_comic_content,
    is_comic_label,
    is_comic_labels,
    is_comic_source,
    normalize_kind,
    reclassify_books,
    resolve_kind,
)
from app.services.storage import BookStorage


def _source(config):
    return SimpleNamespace(id="s1", config=config)


def test_image_source_type_marks_comics():
    assert is_comic_source(_source({"bookSourceType": 2})) is True
    assert is_comic_source(_source({"bookSourceType": "2"})) is True
    assert is_comic_source(_source({"bookSourceType": 0})) is False
    assert is_comic_source(_source(None)) is False
    assert is_comic_source(None) is False
    # A plain dict is accepted too (the import path passes raw source JSON).
    assert is_comic_source({"bookSourceType": 2}) is True


def test_label_keywords_match_comic_genres():
    assert is_comic_label("漫画") is True
    assert is_comic_label(" 漫畫 ") is True
    assert is_comic_label("写真") is True
    assert is_comic_label("Manga") is True
    assert is_comic_labels(["玄幻", "漫画"]) is True
    # 動漫改編 ("anime adaptation") is a novel genre, not a comic marker.
    assert is_comic_label("動漫改編") is False
    assert is_comic_label("都市") is False
    assert is_comic_label("") is False


def test_image_only_chapter_is_a_comic_page():
    manga = (
        "#全话阅读\n\n"
        '<img src="/api/chapters/abc/images/1.webp" alt="001" loading="lazy">\n'
        '<img src="/api/chapters/abc/images/2.webp" alt="002" loading="lazy">\n'
    )
    assert is_comic_content(manga) is True
    assert is_comic_content("正文只有文字，没有任何图片。") is False
    assert is_comic_content(None) is False
    assert is_comic_content("") is False


def test_prose_with_one_illustration_is_still_a_novel():
    chapter = (
        "#第一章\n\n"
        "他把信折好放进抽屉，窗外雨声渐密，故事从这里开始。\n" * 12
        + '![插图](/api/chapters/abc/images/9.webp)\n'
    )
    assert is_comic_content(chapter) is False
    assert resolve_kind(labels=["玄幻"], content=chapter, current=KIND_NOVEL) == KIND_NOVEL


def test_resolve_kind_only_promotes_to_comic():
    assert resolve_kind(source=_source({"bookSourceType": 2})) == KIND_COMIC
    assert resolve_kind(labels=["图集"]) == KIND_COMIC
    assert (
        resolve_kind(
            content='<img src="/api/chapters/a/images/1.webp">',
            current=KIND_NOVEL,
        )
        == KIND_COMIC
    )
    # Nothing matched: keep whatever the book already was.
    assert resolve_kind(current=KIND_COMIC) == KIND_COMIC
    assert resolve_kind() == KIND_NOVEL
    assert normalize_kind("COMIC") == KIND_COMIC
    assert normalize_kind("nonsense") == KIND_NOVEL
    assert normalize_kind(None) == KIND_NOVEL


def test_manga_album_sample_from_gongshen_manga():
    """Regression shape: 绅士漫画 albums are ``<img>`` lines under a title."""
    chapter = "\n".join(
        ["#全话阅读", ""]
        + [
            f'<img src="/api/chapters/455b2fb4/images/{index}.webp" alt="{index:03d}" loading="lazy">'
            for index in range(1, 40)
        ]
    )
    assert is_comic_content(chapter) is True


def test_classify_book_uses_stored_tags_and_categories():
    manga_book = SimpleNamespace(
        kind=KIND_NOVEL,
        tag_names=["漫画", "r18"],
        category_names=[],
    )
    assert classify_book(manga_book) == KIND_COMIC

    gallery_book = SimpleNamespace(
        kind=KIND_NOVEL,
        tag_names=["r18"],
        category_names=["图集"],
    )
    assert classify_book(gallery_book) == KIND_COMIC

    novel_book = SimpleNamespace(
        kind=KIND_NOVEL,
        tag_names=["玄幻", "r18"],
        category_names=["都市"],
    )
    assert classify_book(novel_book) == KIND_NOVEL

    already_comic = SimpleNamespace(
        kind=KIND_COMIC,
        tag_names=["玄幻"],
        category_names=["都市"],
    )
    assert classify_book(already_comic) == KIND_COMIC


def test_kind_filter_only_applies_known_kinds():
    from sqlalchemy import select

    from app.api.routes.books import _apply_kind_filter
    from app.models import Book

    filtered = _apply_kind_filter(select(Book), "comic")
    assert "books.kind = " in str(filtered)
    assert "comic" in filtered.compile().params.values()
    assert "books.kind = " not in str(_apply_kind_filter(select(Book), ""))
    assert "books.kind = " not in str(_apply_kind_filter(select(Book), "all"))
    assert "books.kind = " not in str(_apply_kind_filter(select(Book), None))


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def unique(self):
        return self


class _FakeSession:
    """Just enough AsyncSession for ``reclassify_books``."""

    def __init__(self, sources, book_batches, chapter_rows):
        self._sources = sources
        self._book_batches = list(book_batches)
        self._chapter_rows = chapter_rows
        self.commits = 0

    async def scalars(self, query):
        if "FROM sources" in str(query):
            return _FakeResult(self._sources)
        if self._book_batches:
            return _FakeResult(self._book_batches.pop(0))
        return _FakeResult([])

    async def execute(self, query):
        return _FakeResult(self._chapter_rows)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_reclassify_books_backfills_and_reads_chapters(monkeypatch):
    source = SimpleNamespace(id="s-text", config={"bookSourceType": 0})
    plain = SimpleNamespace(
        id="b1", source_id="s-text", kind=KIND_NOVEL,
        tag_names=["玄幻"], category_names=["都市"],
    )
    labelled = SimpleNamespace(
        id="b2", source_id="s-text", kind=KIND_NOVEL,
        tag_names=["漫画"], category_names=[],
    )
    gallery = SimpleNamespace(
        id="b3", source_id="s-text", kind=KIND_NOVEL,
        tag_names=["r18"], category_names=[],
    )
    already_comic = SimpleNamespace(
        id="b4", source_id="s-text", kind=KIND_COMIC,
        tag_names=[], category_names=[],
    )
    content = {
        "/storage/b1.md": "#第一章\n\n" + "正文正文正文" * 40,
        "/storage/b3.md": '<img src="/api/chapters/x/images/1.webp" alt="001">',
    }
    monkeypatch.setattr(
        BookStorage,
        "read_chapter",
        lambda self, path: content[path],
    )

    session = _FakeSession(
        [source],
        [[plain, labelled, gallery, already_comic]],
        [("b1", "/storage/b1.md"), ("b3", "/storage/b3.md")],
    )
    result = await reclassify_books(session, scan_content=True)

    assert plain.kind == KIND_NOVEL
    assert labelled.kind == KIND_COMIC
    assert gallery.kind == KIND_COMIC
    assert already_comic.kind == KIND_COMIC
    assert result["books"] == 4
    assert result["comics"] == 3
    assert result["novels"] == 1
    assert result["updated"] == 2
    assert result["scanned"] == 2
    assert result["content_comics"] == 1
    # One commit per processed chunk; the empty follow-up chunk ends the loop.
    assert session.commits == 1


@pytest.mark.asyncio
async def test_reclassify_books_can_skip_content_scan(monkeypatch):
    source = SimpleNamespace(id="s-text", config={"bookSourceType": 0})
    gallery = SimpleNamespace(
        id="b1", source_id="s-text", kind=KIND_NOVEL,
        tag_names=[], category_names=[],
    )
    monkeypatch.setattr(
        BookStorage,
        "read_chapter",
        lambda self, path: pytest.fail("content scan should be skipped"),
    )
    session = _FakeSession([source], [[gallery]], [("b1", "/storage/b1.md")])

    result = await reclassify_books(session, scan_content=False)

    assert gallery.kind == KIND_NOVEL
    assert result["scanned"] == 0
    assert result["comics"] == 0
    assert result["novels"] == 1


@pytest.mark.asyncio
async def test_reclassify_books_force_demotes_a_wrong_comic(monkeypatch):
    """A mis-detected comic becomes a novel again only in strict mode."""
    source = SimpleNamespace(id="s-text", config={"bookSourceType": 0})
    wrong = SimpleNamespace(
        id="b1", source_id="s-text", kind=KIND_COMIC,
        tag_names=["玄幻"], category_names=["都市"],
    )
    monkeypatch.setattr(
        BookStorage,
        "read_chapter",
        lambda self, path: "#第一章\n\n" + "正文正文正文" * 40,
    )

    keep = await reclassify_books(
        _FakeSession([source], [[wrong]], [("b1", "/storage/b1.md")]),
        scan_content=True,
    )
    assert wrong.kind == KIND_COMIC  # upgrade-only by default
    assert keep["updated"] == 0

    forced = await reclassify_books(
        _FakeSession([source], [[wrong]], [("b1", "/storage/b1.md")]),
        scan_content=True,
        force=True,
    )
    assert wrong.kind == KIND_NOVEL
    assert forced["updated"] == 1
    assert forced["novels"] == 1
