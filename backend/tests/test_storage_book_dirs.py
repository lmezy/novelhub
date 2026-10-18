"""One book owns one storage directory.

Chapter files were keyed by ``books/<author>/<title>/<number>.md`` only, so two
books that share an author and a title (several sources carry the same book, and
one source can carry two books with the same title) wrote the same file: the
later sync replaced the other book's chapter text.  The live library proved it
-- four copies of 《好色人妻》 all pointed at
``books/燃烧一切/好色人妻/00000N.md`` and the bytes on disk matched only one of
them -- so these tests pin the ownership rule that keeps them apart.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.crawler.base import RemoteChapter
from app.models import Chapter
from app.services.storage import BookRef, BookStorage
from app.services.sync import SyncService

BOOK_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
BOOK_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"


def _ref(book_id: str, source_id: str, source_book_id: str) -> BookRef:
    return BookRef(
        book_id=book_id,
        source_id=source_id,
        source_book_id=source_book_id,
    )


def test_two_books_sharing_an_author_and_title_get_their_own_directories(tmp_path):
    storage = BookStorage(str(tmp_path))
    ref_a = _ref(BOOK_A, "yuedu_alice", "https://www.alicesw.com/novel/53309.html")
    ref_b = _ref(BOOK_B, "yuedu_bb", "https://bbxxxx.com/novel/62756")

    meta_a = {"book_id": BOOK_A, "source_id": ref_a.source_id,
              "source_book_id": ref_a.source_book_id}
    meta_b = {"book_id": BOOK_B, "source_id": ref_b.source_id,
              "source_book_id": ref_b.source_book_id}

    path_a = storage.write_metadata("燃烧一切", "好色人妻", meta_a, ref=ref_a)
    path_b = storage.write_metadata("燃烧一切", "好色人妻", meta_b, ref=ref_b)

    # The first book keeps the historical layout; the second is pushed aside.
    assert path_a.parent.name == "好色人妻"
    assert path_b.parent.name == "好色人妻~bbbbbbbb"

    chapter_a, _ = storage.write_chapter(
        "燃烧一切", "好色人妻", 1, "第一章", "A 的正文", ref=ref_a,
    )
    chapter_b, _ = storage.write_chapter(
        "燃烧一切", "好色人妻", 1, "第一章", "B 的正文", ref=ref_b,
    )
    assert chapter_a != chapter_b
    assert "A 的正文" in storage.read_chapter(chapter_a)
    assert "B 的正文" in storage.read_chapter(chapter_b)

    # A re-sync of the owning book stays in its own directory.
    again, _ = storage.write_chapter(
        "燃烧一切", "好色人妻", 1, "第一章", "A 的正文改", ref=ref_a,
    )
    assert again == chapter_a


def test_legacy_metadata_without_book_id_still_claims_its_directory(tmp_path):
    """Files written before ``book_id`` was recorded must not move."""
    storage = BookStorage(str(tmp_path))
    legacy_dir = tmp_path / "艾欧尼亚之光" / "丝袜高跟良家沉沦记"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "metadata.json").write_text(
        json.dumps(
            {
                "source_id": "yuedu_alice",
                "source_book_id": "https://www.alicesw.com/novel/52829.html",
                "title": "丝袜高跟良家沉沦记",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    mine = _ref(BOOK_A, "yuedu_alice", "https://www.alicesw.com/novel/52829.html")
    assert storage.book_dir("艾欧尼亚之光", "丝袜高跟良家沉沦记", mine) == legacy_dir

    # Another source's copy of the same book is not the owner.
    other = _ref(BOOK_B, "yuedu_bb", "https://bbxxxx.com/novel/1")
    assert storage.book_dir("艾欧尼亚之光", "丝袜高跟良家沉沦记", other) != legacy_dir


def test_unclaimed_directory_is_claimed_even_without_metadata(tmp_path):
    storage = BookStorage(str(tmp_path))
    ref = _ref(BOOK_A, "yuedu_alice", "https://www.alicesw.com/novel/1")
    assert storage.book_dir("匿名", "第一章", ref) == tmp_path / "匿名" / "第一章"

    # ``metadata.json`` without any identity (hand-made folders) is claimable.
    directory = tmp_path / "匿名" / "随手写的"
    directory.mkdir(parents=True)
    (directory / "metadata.json").write_text('{"title": "随手写的"}', encoding="utf-8")
    assert storage.book_dir("匿名", "随手写的", ref) == directory


def test_book_dir_without_ref_keeps_the_legacy_layout(tmp_path):
    storage = BookStorage(str(tmp_path))
    assert storage.book_dir("作者", "书名") == tmp_path / "作者" / "书名"


def test_chapter_overwritten_by_another_book_is_refetched(tmp_path):
    storage = BookStorage(str(tmp_path))
    ref = _ref(BOOK_A, "yuedu_alice", "https://www.alicesw.com/novel/1")
    content_path, content_hash = storage.write_chapter(
        "作者", "书名", 1, "第一章", "真实正文" * 5, ref=ref,
    )
    chapter = Chapter(
        id="c1",
        book_id=BOOK_A,
        chapter_number=1,
        source_chapter_id="https://www.alicesw.com/book/1/a.html",
        title="第一章",
        content_path=content_path,
        hash=content_hash,
    )
    service = SyncService.__new__(SyncService)
    service.storage = storage

    assert service._chapter_has_real_content(chapter) is True

    # Another book's sync replaces the file: the stored digest no longer
    # matches, which is a proof rather than a guess.
    (tmp_path / "作者" / "书名" / "000001.md").write_text(
        "#第一章\n\n别人的正文\n", encoding="utf-8",
    )
    assert service._chapter_has_real_content(chapter) is False


@pytest.mark.asyncio
async def test_foreign_repair_is_counted_so_it_is_not_silent(tmp_path):
    """The sync reports how many chapters it re-downloaded for this reason."""
    storage = BookStorage(str(tmp_path))
    ref = _ref(BOOK_A, "yuedu_alice", "https://www.alicesw.com/novel/1")
    content_path, content_hash = storage.write_chapter(
        "作者", "书名", 1, "第一章", "真实正文" * 5, ref=ref,
    )
    chapter = Chapter(
        id="c1",
        book_id=BOOK_A,
        chapter_number=1,
        source_chapter_id="https://www.alicesw.com/book/1/a.html",
        title="第一章",
        content_path=content_path,
        hash=content_hash,
    )
    remote = [
        RemoteChapter(
            source_chapter_id="https://www.alicesw.com/book/1/a.html",
            title="第一章",
            url="https://www.alicesw.com/book/1/a.html",
            chapter_number=1,
        ),
    ]
    # Another book's sync overwrote the file.
    Path(content_path).write_text("#第一章\n\n别人的正文\n", encoding="utf-8")

    db = AsyncMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [chapter]))
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    service = SyncService(db)
    service.storage = storage

    stats: dict = {}
    kept = await service._reconcile_chapter_ids(BOOK_A, remote, stats=stats)

    assert kept == set()
    assert stats["foreign_chapters"] == 1
    assert db.delete.await_count == 1


def test_the_live_four_copies_of_one_title_no_longer_share_a_directory(tmp_path):
    """The incident, replayed with the identities it happened with.

    ``books/燃烧一切/好色人妻`` held one ``metadata.json`` (the BB成人小说 copy)
    while four books pointed their chapters at its files -- 爱丽丝书屋,
    BB成人小说, 一夜春宵 and 要撸小说 all carry 《好色人妻》 by 燃烧一切.
    """
    storage = BookStorage(str(tmp_path))
    owner_dir = tmp_path / "燃烧一切" / "好色人妻"
    owner_dir.mkdir(parents=True)
    (owner_dir / "metadata.json").write_text(
        json.dumps(
            {
                "source_id": "yuedu_5cc2d3c6875a",
                "source_book_id": "https://bbxxxx.com/novel/62756",
                "title": "好色人妻",
                "author": "燃烧一切",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    copies = {
        "yuedu_31a56dc1e2a9": ("19d7eb1a-5667-487d-96d6-102d2629929d",
                               "https://www.alicesw.com/novel/53309.html"),
        "yuedu_5cc2d3c6875a": ("5af01242-ce94-4cec-9725-28335d49333f",
                               "https://bbxxxx.com/novel/62756"),
        "yuedu_220ce328b9b4": ("629ef5e9-a5c6-4095-91e4-09224bb72a87",
                               "https://www.yiyechunxiao.com/book/470670"),
        "yuedu_b38b98d309e3": ("f7615f72-2cc9-4e10-ba84-818a95eb295d",
                               "https://www.yaoluku.com/book/57577"),
    }

    directories = {}
    for source_id, (book_id, source_book_id) in copies.items():
        ref = _ref(book_id, source_id, source_book_id)
        path, _ = storage.write_chapter(
            "燃烧一切", "好色人妻", 1, "第一章", f"{source_id} 的正文", ref=ref,
        )
        directories[source_id] = Path(path).parent

    # The copy whose metadata owns the folder keeps it; the other three move.
    assert directories["yuedu_5cc2d3c6875a"] == owner_dir
    assert len(set(directories.values())) == 4
    for source_id, directory in directories.items():
        if source_id != "yuedu_5cc2d3c6875a":
            assert directory.name.startswith("好色人妻~")
    for source_id, directory in directories.items():
        stored = storage.read_chapter(str(directory / "000001.md"))
        assert f"{source_id} 的正文" in stored
