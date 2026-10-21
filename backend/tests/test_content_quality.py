"""Cover-wall chapters: detect, skip on sync, and quarantine without deleting."""
from types import SimpleNamespace

from sqlalchemy import select

from app.models import Book
from app.services.content_quality import (
    MIN_TEXT_CHARS,
    cover_wall_matches,
    normalize_title,
    visible_text,
)
from app.services.visibility import apply_book_visibility

OTHER_TITLES = ["系统，解锁特殊cg真的能变强吗？", "世上只有妈妈好", "降服骚脚榨汁干妈"]
WALL = (
    '<img src="/a.webp" alt="系统，解锁特殊cg真的能变强吗？">'
    '<img src="/b.webp" alt="世上只有妈妈好">'
    '<img src="/c.webp" alt="降服骚脚榨汁干妈">'
)


def test_a_cover_wall_names_the_other_books():
    assert cover_wall_matches(WALL, OTHER_TITLES, "隐形的主人") == [
        "系统，解锁特殊cg真的能变强吗？",
        "世上只有妈妈好",
        "降服骚脚榨汁干妈",
    ]


def test_a_real_chapter_with_images_is_kept():
    body = WALL + "这一章的正文很长，讲的是主角在雨夜里做出了决定。" * 3
    assert len(visible_text(body)) >= MIN_TEXT_CHARS
    assert cover_wall_matches(body, OTHER_TITLES, "隐形的主人") == []


def test_one_matched_title_is_not_enough():
    body = '<img alt="世上只有妈妈好"><img alt="未知的作品">'
    assert cover_wall_matches(body, OTHER_TITLES, "隐形的主人") == []


def test_the_books_own_cover_is_not_a_wall():
    body = '<img alt="隐形的主人"><img alt="隐形的主人">'
    assert cover_wall_matches(body, OTHER_TITLES, "隐形的主人") == []


def test_markup_is_ignored_and_titles_fold():
    assert normalize_title("《隐形的主人》（1-2） 作者：art_dino") == "隐形的主人12作者art_dino"
    # The chapter heading alone never reaches the text threshold.
    body = "#【隐形的主人】【作者：art_dino】" + WALL
    assert cover_wall_matches(body, OTHER_TITLES, "隐形的主人")


def test_hidden_books_are_invisible_to_admins_too():
    admin = SimpleNamespace(id="u1", role="super_admin", r18_enabled=True,
                            non_r18_enabled=True, allow_all_ages=True, show_r18=True)
    sql = str(apply_book_visibility(select(Book), admin))

    assert "books.hidden" in sql
