"""Tests for novel/comic classification (``app.services.book_kind``)."""

import pytest
from types import SimpleNamespace

from app.services import book_kind as book_kind_module
from app.services.book_kind import (
    KIND_COMIC,
    KIND_NOVEL,
    chapter_comic_vote,
    classify_book,
    content_verdict,
    is_comic_content,
    is_ambiguous_chapter,
    is_comic_label,
    is_comic_labels,
    is_comic_source,
    normalize_kind,
    reclassify_books,
    resolve_kind,
    sample_chapter_indexes,
    should_promote_to_comic,
)
from app.services.storage import BookStorage


def _gallery(pages: int = 12) -> str:
    """A real album chapter: a whole gallery of images under a title."""
    return "\n".join(
        ["#全话阅读", ""]
        + [
            f'<img src="/api/chapters/abc/images/{i}.webp" alt="{i:03d}" loading="lazy">'
            for i in range(1, pages + 1)
        ]
    )


def _prose() -> str:
    return "#第一章\n\n" + "正文正文正文" * 40


#: What novel sources put in front of chapter 1: one image, no words.  The
#: real ones seen in the library were a site's 「搜索」 icon or an unrelated SEO
#: picture, under an ordinary chapter title.
PLACEHOLDER = "#第一章\n\n" + '<img src="/api/chapters/abc/images/f1b249e054d12455.png" alt="搜索">'

#: Legado merges an image source's pages into one chapter titled 全话阅读.
#: Some albums only ever sync one page, so "one image" alone must not be read
#: as a placeholder -- the title is what tells the two apart.
SINGLE_PAGE_ALBUM = (
    "#全话阅读\n\n"
    '<img src="/api/chapters/abc/images/5929e17b20945fc4.jpg" alt="01_01_rxtd" loading="lazy">\n'
)


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
    assert resolve_kind(content=_gallery(), current=KIND_NOVEL) == KIND_COMIC
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


class _ProseBook:
    def __init__(self, kind=KIND_NOVEL, tags=(), categories=()):
        self.kind = kind
        self.tag_names = list(tags)
        self.category_names = list(categories)


def test_a_lone_cover_image_abstains_instead_of_voting():
    """One image, no words, no album marker: a novel's placeholder or a
    one-page album -- the page itself cannot say which, so it does not vote.
    """
    assert is_ambiguous_chapter(PLACEHOLDER) is True
    assert is_ambiguous_chapter(_gallery()) is False
    assert is_ambiguous_chapter(_prose()) is False
    assert chapter_comic_vote(PLACEHOLDER) is None
    assert chapter_comic_vote(_gallery()) is True
    assert chapter_comic_vote(_prose()) is False


def test_a_one_page_album_is_still_a_gallery():
    """Titled 全话阅读 -- Legado's marker for an image source.

    Most of the library's comics are single-chapter books, and a good many of
    those only ever synced one page, so this shape has to vote "comic".
    """
    assert is_ambiguous_chapter(SINGLE_PAGE_ALBUM) is False
    assert chapter_comic_vote(SINGLE_PAGE_ALBUM) is True
    assert content_verdict([chapter_comic_vote(SINGLE_PAGE_ALBUM)]) is True


def test_a_one_chapter_book_of_nothing_but_an_ambiguous_image_keeps_its_kind():
    """Nothing decisive to read: no verdict, so the stored value survives."""
    assert content_verdict([chapter_comic_vote(PLACEHOLDER)]) is None


def test_placeholder_first_chapter_does_not_make_the_book_a_comic():
    """20 novels were locked as comics by a placeholder image in chapter 1.

    The placeholder abstains; the prose chapters behind it carry the vote.
    """
    votes = [v for v in [chapter_comic_vote(PLACEHOLDER)] + [False] * 5 if v is not None]
    assert content_verdict(votes) is False


def test_majority_of_galleries_does_make_the_book_a_comic():
    assert content_verdict([chapter_comic_vote(_gallery())] * 6) is True


def test_no_readable_chapter_leaves_the_verdict_unknown():
    # A comic that has not synced yet must not be re-labelled as a novel.
    assert content_verdict([]) is None


def test_content_clears_a_wrong_comic_label():
    """45 novels were locked as comics by a stray 漫画 tag."""
    book = _ProseBook(kind=KIND_COMIC, tags=["漫画"], categories=["漫画"])
    assert classify_book(book, content_votes=[False] * 4) == KIND_NOVEL


def test_labels_decide_when_there_is_nothing_to_read():
    book = _ProseBook(kind=KIND_COMIC, tags=["漫画"])
    assert classify_book(book, content_votes=[]) == KIND_COMIC
    # No label and nothing readable: keep the stored value rather than guess.
    assert classify_book(_ProseBook(kind=KIND_COMIC), content_votes=[]) == KIND_COMIC


def test_sync_promotes_only_after_several_chapters_agree():
    """A sync can only upgrade, so one chapter must never be enough."""
    assert should_promote_to_comic([]) is False
    assert should_promote_to_comic([True]) is False
    assert should_promote_to_comic([chapter_comic_vote(PLACEHOLDER)]) is False
    assert should_promote_to_comic([True, True]) is True
    assert should_promote_to_comic([True, False, False, False]) is False


def test_sampling_spreads_over_the_book_and_keeps_the_first_chapter():
    indexes = sample_chapter_indexes(60)
    assert indexes[0] == 0
    assert indexes == sorted(set(indexes))
    assert sample_chapter_indexes(3) == [0, 1, 2]
    assert sample_chapter_indexes(0) == []


@pytest.mark.asyncio
async def test_scan_book_votes_reports_reads_including_abstentions(tmp_path):
    """Abstentions cost a read without yielding a vote; both numbers matter."""
    from app.services.book_kind import scan_book_votes

    prose = tmp_path / "prose.md"
    prose.write_text("# 第二章\n\n" + "正文。" * 100, encoding="utf-8")
    placeholder = tmp_path / "placeholder.md"
    placeholder.write_text(PLACEHOLDER, encoding="utf-8")

    storage = BookStorage()
    votes, reads = await scan_book_votes(
        storage, [str(placeholder), str(prose)], limit=2
    )
    assert votes == [False]  # placeholder abstains, prose votes novel
    assert reads == 2


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

    async def scalar(self, query):
        # ``reclassify_books`` counts the books up front for progress reporting.
        if "count(*)" in str(query).lower():
            return sum(len(batch) for batch in self._book_batches)
        return None

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
        "/storage/b1.md": _prose(),
        "/storage/b3.md": _gallery(),
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
    assert labelled.kind == KIND_COMIC  # tag says comic, no chapters to gainsay it
    assert gallery.kind == KIND_COMIC  # content says comic
    assert already_comic.kind == KIND_COMIC  # unsynced: the stored value survives
    assert result["books"] == 4
    assert result["comics"] == 3
    assert result["novels"] == 1
    # b2 moved on its label, b3 on its chapters; b4 lost nothing for want of
    # chapters to read.
    assert result["updated"] == 2
    assert result["scanned"] == 2
    assert result["content_comics"] == 1
    assert result["no_chapters"] == 2
    # Only the books whose kind moved are offered to the search index refresh.
    assert result["changed_book_ids"] == ["b2", "b3"]
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
async def test_reclassify_books_demotes_a_wrong_comic(monkeypatch):
    """A mis-detected comic becomes a novel again -- content beats the label.

    This is the "重新识别 did nothing" bug: the novels were prose on disk but
    stayed comics because the old rules only ever promoted.  ``force`` is no
    longer what unlocks it; the chapters are.
    """
    source = SimpleNamespace(id="s-text", config={"bookSourceType": 0})
    wrong = SimpleNamespace(
        id="b1", source_id="s-text", kind=KIND_COMIC,
        tag_names=["玄幻"], category_names=["都市"],
    )
    monkeypatch.setattr(BookStorage, "read_chapter", lambda self, path: _prose())

    result = await reclassify_books(
        _FakeSession([source], [[wrong]], [("b1", "/storage/b1.md")]),
        scan_content=True,
    )
    assert wrong.kind == KIND_NOVEL
    assert result["updated"] == 1
    assert result["novels"] == 1
    assert result["changed_book_ids"] == ["b1"]


@pytest.mark.asyncio
async def test_reclassify_books_rescues_a_comic_label_locked_novel(monkeypatch):
    """A 漫画 tag must not survive chapters that are demonstrably prose."""
    source = SimpleNamespace(id="s-text", config={"bookSourceType": 0})
    locked = SimpleNamespace(
        id="b1", source_id="s-text", kind=KIND_COMIC,
        tag_names=["漫画"], category_names=["漫画"],
    )
    monkeypatch.setattr(BookStorage, "read_chapter", lambda self, path: _prose())

    result = await reclassify_books(
        _FakeSession([source], [[locked]], [("b1", "/storage/b1.md")]),
        scan_content=True,
    )
    assert locked.kind == KIND_NOVEL
    assert result["updated"] == 1


@pytest.mark.asyncio
async def test_reclassify_books_rescues_a_placeholder_locked_novel(monkeypatch):
    """Chapter 1 is a cover image, the rest is prose: still a novel."""
    source = SimpleNamespace(id="s-text", config={"bookSourceType": 0})
    locked = SimpleNamespace(
        id="b1", source_id="s-text", kind=KIND_COMIC,
        tag_names=[], category_names=[],
    )
    paths = [f"/storage/b1/{i}.md" for i in range(6)]
    chapters = [("b1", p) for p in paths]

    def read(self, path):
        return PLACEHOLDER if path == paths[0] else _prose()

    monkeypatch.setattr(BookStorage, "read_chapter", read)

    result = await reclassify_books(
        _FakeSession([source], [[locked]], chapters),
        scan_content=True,
    )
    assert locked.kind == KIND_NOVEL
    assert result["updated"] == 1


@pytest.mark.asyncio
async def test_reclassify_books_keeps_an_unsynced_comic(monkeypatch):
    """No chapters on disk is not evidence of prose -- do not flip it."""
    source = SimpleNamespace(id="s-text", config={"bookSourceType": 0})
    comic = SimpleNamespace(
        id="b1", source_id="s-text", kind=KIND_COMIC,
        tag_names=["漫画"], category_names=[],
    )
    monkeypatch.setattr(
        BookStorage, "read_chapter", lambda self, path: pytest.fail("no chapters")
    )

    result = await reclassify_books(
        _FakeSession([source], [[comic]], []),
        scan_content=True,
        force=True,
    )
    assert comic.kind == KIND_COMIC
    assert result["updated"] == 0
    assert result["no_chapters"] == 1


@pytest.mark.asyncio
async def test_reclassify_books_reports_progress_and_can_be_stopped(monkeypatch):
    source = SimpleNamespace(id="s-text", config={"bookSourceType": 0})
    book = SimpleNamespace(
        id="b1", source_id="s-text", kind=KIND_NOVEL,
        tag_names=[], category_names=[],
    )
    monkeypatch.setattr(BookStorage, "read_chapter", lambda self, path: _prose())
    updates = []

    result = await reclassify_books(
        _FakeSession([source], [[book]], [("b1", "/storage/b1.md")]),
        scan_content=True,
        progress=updates.append,
        should_stop=lambda: True,
    )
    assert result["cancelled"] is True
    assert result["books"] == 0
