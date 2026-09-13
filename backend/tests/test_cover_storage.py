import os

from app.services.storage import MAX_SEGMENT_BYTES, BookStorage, safe_segment


LONG_MANGA_TITLE = (
    "[とろとろ夢ばなな (夢木ばなな)]「あれぇ、ちょっと舐めたらめっちゃ勃起してんじゃんw」"
    " 【悲報】女叩き男さん、極上女体でオマ●コ堕ち｜「哎呀、稍微舔了一下立马就勃起了呢W」"
    "~「【悲报】厌女男，堕落于至上女体的小穴之下」 [白杨汉化组] [LKM渣嵌]"
)


def test_safe_segment_keeps_short_titles_unchanged():
    assert safe_segment("Book One") == "Book One"
    assert safe_segment("") == "unknown"
    assert safe_segment('a/b:c*d?e"f<g>h|i') == "a_b_c_d_e_f_g_h_i"


def test_safe_segment_shortens_titles_that_exceed_the_filesystem_limit():
    segment = safe_segment(LONG_MANGA_TITLE)

    assert len(segment.encode("utf-8")) <= MAX_SEGMENT_BYTES
    assert segment != safe_segment(LONG_MANGA_TITLE + " (2)")
    # Stable: the same title always maps to the same directory.
    assert segment == safe_segment(LONG_MANGA_TITLE)
    # No half-encoded characters left behind.
    assert segment.encode("utf-8").decode("utf-8") == segment


def test_long_manga_title_can_be_written_to_disk(tmp_path):
    storage = BookStorage(str(tmp_path / "books"))

    path, _hash = storage.write_chapter(
        "Unknown",
        LONG_MANGA_TITLE,
        1,
        "全话阅读",
        "![002](https://example.com/002.jpg)",
    )

    component = os.path.basename(os.path.dirname(path))
    assert len(component.encode("utf-8")) <= MAX_SEGMENT_BYTES
    assert os.path.exists(path)


def test_save_cover_detects_extension_and_returns_relative_path(tmp_path):
    storage = BookStorage(str(tmp_path / "books"))

    path = storage.save_cover("book-1", b"\xff\xd8\xff\xe0test")

    assert path == "covers/book-1.jpg"
    assert (tmp_path / "covers" / "book-1.jpg").read_bytes() == b"\xff\xd8\xff\xe0test"


def test_save_cover_detects_png(tmp_path):
    storage = BookStorage(str(tmp_path / "books"))

    path = storage.save_cover("book-2", b"\x89PNG\r\n\x1a\n")

    assert path == "covers/book-2.png"


def test_save_display_cover_keeps_source_cover(tmp_path):
    storage = BookStorage(str(tmp_path / "books"))
    source_path = storage.save_cover("book-1", b"\xff\xd8\xff\xe0source")
    display_path = storage.save_display_cover("book-1", b"\x89PNG\r\n\x1a\ndisplay")

    assert source_path == "covers/book-1.jpg"
    assert display_path == "covers/book-1_display.png"
    assert (tmp_path / "covers" / "book-1.jpg").exists()
    assert (tmp_path / "covers" / "book-1_display.png").exists()


def test_save_chapter_image_returns_relative_path(tmp_path):
    storage = BookStorage(str(tmp_path / "books"))

    rel = storage.save_chapter_image(
        "book-1",
        "https://example.com/pic.jpg",
        b"\xff\xd8\xff\xe0img",
    )

    assert rel.startswith("book-1/images/")
    assert rel.endswith(".jpg")
    assert (tmp_path / "books" / rel).read_bytes() == b"\xff\xd8\xff\xe0img"


def test_chapter_image_path_rejects_traversal(tmp_path):
    storage = BookStorage(str(tmp_path / "books"))
    storage.save_chapter_image(
        "book-1",
        "https://example.com/pic.jpg",
        b"\xff\xd8\xff\xe0img",
    )

    try:
        storage.chapter_image_path("book-1", "../outside.jpg")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Path traversal should be rejected")
