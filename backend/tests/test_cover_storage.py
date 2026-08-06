from app.services.storage import BookStorage


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
