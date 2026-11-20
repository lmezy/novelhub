from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import shutil
import re
from uuid import uuid4
from loguru import logger

from app.core.config import settings


def looks_like_text_response(data: bytes) -> bool:
    header = data[:256].lstrip().lower()
    return header.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"<script", b"{", b"["))


def _atomic_write(path: Path, data: bytes) -> None:
    """Publish the complete file with one same-directory rename."""
    temporary = path.with_name(f".novelhub-{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class TruncatedChapterError(ValueError):
    """A refresh returned only the opening of the stored chapter."""


class ChapterFileBatch:
    """Undo published chapter files if their database transaction rolls back."""

    def __init__(self):
        self._originals: dict[Path, Path | None] = {}

    def write(self, numbered: Path, data: bytes, chapter_id: str, previous: str | None) -> Path:
        # A TOC insertion can reuse a number still owned by a different
        # chapter. Retain our own filename or reserve an unused one.
        path = numbered
        old = Path(previous) if previous else None
        if old and old.parent == numbered.parent and old.suffix == ".md":
            path = old
        if path != old:
            try:
                path.touch(exist_ok=False)
            except FileExistsError:
                path = numbered.with_stem(f"{numbered.stem}~{safe_segment(chapter_id)}")
                path.touch(exist_ok=False)
            self._originals[path] = None
        elif path not in self._originals:
            backup = path.with_name(f".novelhub-{uuid4().hex}.bak")
            try:
                os.link(path, backup)
            except FileNotFoundError:
                backup = None
            except OSError:
                # Some NAS volumes do not support hard links.
                try:
                    shutil.copy2(path, backup)
                except BaseException:
                    backup.unlink(missing_ok=True)
                    raise
            self._originals[path] = backup
        _atomic_write(path, data)
        return path

    def finish(self, *, committed: bool) -> None:
        restore_error = None
        for path, original in self._originals.items():
            if committed:
                if original is not None:
                    try:
                        original.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.warning("Could not remove committed chapter backup {}: {}", original, exc)
            else:
                try:
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        original.replace(path)
                        original.unlink(missing_ok=True)
                except OSError as exc:
                    logger.error("Could not restore {} from {}: {}", path, original, exc)
                    restore_error = exc
        self._originals.clear()
        if restore_error is not None:
            raise restore_error


def chapter_markdown(chapter_title: str, content: str) -> str:
    """The stored form of one chapter: a ``#`` title line, then the body."""
    return f"#{chapter_title}\n\n{content.strip()}\n"


def text_digest(text: str) -> str:
    """sha256 of chapter markdown, decoded-text based (see ``chapter_digest``).

    One helper for the three places that need the same number: ``write_chapter``
    stores it on the row, ``chapter_digest`` re-derives it from a file, and
    ``SyncService._chapter_file_is_ours`` re-derives it from text it has already
    read -- which is what keeps a routine sync at one file read per chapter
    instead of two.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Filesystems cap a single path component at 255 bytes, which long
# Japanese/Chinese titles blow past: syncing 绅士漫画 failed a whole book with
# ``[Errno 36] File name too long`` for titles such as
# 「[とろとろ夢ばなな (夢木ばなな)]「あれぇ、ちょっと舐めたら…」」.
# 240 leaves room for the ``.md``/``_display.png`` suffixes and stays valid on
# the strictest filesystems in use.
MAX_SEGMENT_BYTES = 240


def safe_segment(value: str) -> str:
    normalized = re.sub(r'[\\/:*?"<>|]+', "_", value).strip()
    normalized = normalized or "unknown"
    return _truncate_segment(normalized)


def _truncate_segment(segment: str, limit: int = MAX_SEGMENT_BYTES) -> str:
    """Shorten a path segment to ``limit`` bytes without splitting a character.

    A digest of the full value keeps two different long titles from collapsing
    into the same directory, and the same title always maps to the same name,
    so re-syncs keep writing to the folder they created before.
    """
    encoded = segment.encode("utf-8")
    if len(encoded) <= limit:
        return segment

    digest = hashlib.sha1(encoded).hexdigest()[:8]
    suffix = f"~{digest}"
    budget = max(1, limit - len(suffix.encode("utf-8")))
    truncated = encoded[:budget]
    while truncated:
        try:
            head = truncated.decode("utf-8")
            break
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    else:
        head = ""
    return f"{head}{suffix}"


@dataclass(frozen=True)
class BookRef:
    """Storage identity of one book row.

    Chapter files used to be keyed by ``(author, title, chapter number)``
    alone, which is not unique: several book sources can carry the same book,
    and one source can carry two books that share an author and a title.  They
    all wrote ``books/<author>/<title>/000001.md``, so whichever synced last
    replaced the other's chapter with its own text.  The live library proves
    it: four copies of 《好色人妻》 (爱丽丝书屋 / BB成人小说 / 一夜春宵 /
    要撸小说) all point at ``books/燃烧一切/好色人妻/00000N.md``, and the bytes
    on disk match the stored hash of only one of them.

    A book therefore owns a directory: the plain
    ``books/<author>/<title>`` when it can claim it, and
    ``books/<author>/<title>~<book-id>`` when another book claimed it first.
    ``metadata.json`` records which book claimed a directory (``book_id``),
    falling back to the source reference for files written before that field
    existed, so an upgrade does not move books that never collided.
    """

    book_id: str
    source_id: str = ""
    source_book_id: str = ""

    @property
    def directory_suffix(self) -> str:
        digest = re.sub(r"[^0-9a-zA-Z]", "", str(self.book_id or ""))[:8]
        return f"~{digest}" if digest else ""

    def owns(self, metadata: dict | None) -> bool:
        """Whether a stored ``metadata.json`` belongs to this book."""
        if not isinstance(metadata, dict):
            return False
        recorded = str(metadata.get("book_id") or "")
        if recorded:
            return recorded == str(self.book_id)
        source_id = str(metadata.get("source_id") or "")
        source_book_id = str(metadata.get("source_book_id") or "")
        if not source_id and not source_book_id:
            # A hand-made folder or an import carries no identity at all, so
            # nothing proves it belongs to another book.
            return True
        return bool(
            self.source_id
            and source_id == self.source_id
            and source_book_id == self.source_book_id
        )


class BookStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.STORAGE_PATH)

    def book_dir(
        self,
        author: str,
        title: str,
        ref: BookRef | None = None,
    ) -> Path:
        """Directory that holds a book's chapters and ``metadata.json``.

        Without ``ref`` the historical ``books/<author>/<title>`` is returned
        unchanged (callers that only need the loose layout keep working).  With
        a ``ref`` the directory is chosen so two books can never write the same
        chapter file: a directory nobody claimed -- or one this book already
        owns -- is used as is, an unclaimed-by-us directory gets the book's id
        appended to the title segment.
        """
        base = self.root / safe_segment(author) / safe_segment(title)
        if ref is None:
            return base
        metadata = self.read_metadata(base) if base.exists() else None
        if metadata is None or ref.owns(metadata):
            return base
        segment = _truncate_segment(f"{safe_segment(title)}{ref.directory_suffix}")
        return self.root / safe_segment(author) / segment

    @staticmethod
    def read_metadata(directory: Path) -> dict | None:
        """Parse a book directory's ``metadata.json`` (``None`` if unusable)."""
        try:
            data = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def cover_dir(self) -> Path:
        return self.root.parent / "covers"

    def chapter_images_dir(self, book_id: str) -> Path:
        return self.root / safe_segment(book_id) / "images"

    def previous_chapter_path(self, book_id: str, chapter_id: str) -> Path:
        return self.root / safe_segment(book_id) / ".previous" / (safe_segment(chapter_id) + ".txt")

    @staticmethod
    def _valid_cached_image(path: Path) -> bool:
        try:
            with path.open("rb") as stream:
                header = stream.read(256)
        except (FileNotFoundError, NotADirectoryError):
            return False
        return bool(header) and not looks_like_text_response(header)

    def find_chapter_image(self, book_id: str, url: str) -> str | None:
        directory = self.chapter_images_dir(book_id)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        for extension in ("jpg", "png", "webp", "gif", "svg"):
            path = directory / f"{digest}.{extension}"
            if self._valid_cached_image(path):
                return path.relative_to(self.root).as_posix()
        return None

    def save_chapter_image(self, book_id: str, url: str, data: bytes) -> str:
        """Save one in-content image and return a storage-relative path."""
        if not data or looks_like_text_response(data):
            raise ValueError("图片地址返回了空内容或文本页面")
        directory = self.chapter_images_dir(book_id)
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:16]
        extension = self._image_extension(data)
        path = directory / f"{digest}.{extension}"
        if not self._valid_cached_image(path):
            _atomic_write(path, data)
        return path.relative_to(self.root).as_posix()

    def chapter_image_path(self, book_id: str, filename: str) -> Path:
        """Resolve a stored chapter image path, rejecting path traversal."""
        images_dir = self.chapter_images_dir(book_id).resolve()
        path = (images_dir / filename).resolve()
        if path.parent != images_dir or not path.is_file():
            raise FileNotFoundError(f"Chapter image not found: {filename}")
        return path

    @staticmethod
    def _image_extension(data: bytes) -> str:
        if data.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if data.startswith(b"\x89PNG"):
            return "png"
        if data.startswith(b"GIF8"):
            return "gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "webp"
        if data.lstrip().startswith(b"<svg"):
            return "svg"
        return "jpg"

    def save_cover(self, book_id: str, data: bytes) -> str:
        """Save cover bytes and return a storage-relative path."""
        directory = self.cover_dir()
        directory.mkdir(parents=True, exist_ok=True)
        extension = self._image_extension(data)
        path = directory / f"{safe_segment(book_id)}.{extension}"
        _atomic_write(path, data)
        return path.relative_to(self.root.parent).as_posix()

    def save_display_cover(self, book_id: str, data: bytes) -> str:
        """Save a user-selected cover without overwriting the source cover."""
        directory = self.cover_dir()
        directory.mkdir(parents=True, exist_ok=True)
        extension = self._image_extension(data)
        safe_id = safe_segment(book_id)
        path = directory / f"{safe_id}_display.{extension}"
        _atomic_write(path, data)
        for old in directory.glob(f"{safe_id}_display.*"):
            if old != path:
                old.unlink(missing_ok=True)
        return path.relative_to(self.root.parent).as_posix()

    def write_metadata(
        self,
        author: str,
        title: str,
        metadata: dict,
        ref: BookRef | None = None,
    ) -> Path:
        path = self.book_dir(author, title, ref)
        path.mkdir(parents=True, exist_ok=True)
        metadata_path = path / "metadata.json"
        _atomic_write(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"))
        return metadata_path

    def write_chapter(
        self,
        author: str,
        title: str,
        number: int,
        chapter_title: str,
        content: str,
        ref: BookRef | None = None,
        *,
        chapter_id: str = "",
        previous_path: str | None = None,
        previous_hash: str | None = None,
        batch: ChapterFileBatch | None = None,
    ) -> tuple[str, str]:
        path = self.book_dir(author, title, ref)
        path.mkdir(parents=True, exist_ok=True)
        file_path = path / f"{number:06d}.md"
        markdown = chapter_markdown(chapter_title, content)
        data = markdown.encode("utf-8")
        content_hash = text_digest(markdown)
        if batch is None:
            _atomic_write(file_path, data)
        else:
            if chapter_id and previous_path and previous_hash and previous_hash != content_hash:
                try:
                    old_text = self.read_chapter(previous_path)
                except (FileNotFoundError, UnicodeDecodeError):
                    old_text = None
                # Legacy books can share a file. Only archive text that actually
                # matches this chapter's database row.
                if old_text is not None and text_digest(old_text) == previous_hash:
                    old_body = old_text.partition("\n")[2] if old_text.startswith("#") else old_text
                    old_body = "".join(old_body.split())
                    new_body = "".join(content.split())
                    if len(old_body) >= 800 and 40 <= len(new_body) <= len(old_body) // 4 and old_body.startswith(new_body):
                        raise TruncatedChapterError("新正文只有已存正文的一段开头，已保留原文")
                    history = (self.previous_chapter_path(ref.book_id, chapter_id) if ref
                               else path / ".previous" / (safe_segment(chapter_id) + ".txt"))
                    history.parent.mkdir(parents=True, exist_ok=True)
                    batch.write(history, old_text.encode("utf-8"), chapter_id, str(history))
            file_path = batch.write(file_path, data, chapter_id, previous_path)
        return str(file_path), content_hash

    def read_chapter(self, content_path: str) -> str:
        return Path(content_path).read_text(encoding="utf-8")

    def chapter_digest(self, content_path: str) -> str | None:
        """sha256 of a stored chapter's text, or ``None`` when unreadable.

        ``Chapter.hash`` holds the digest ``write_chapter`` computed over the
        markdown it was handed, so a digest that no longer matches proves the
        file is not what this row wrote -- another book overwrote it in the
        shared-directory era.

        The digest is taken over the *decoded text*, exactly like
        ``write_chapter`` did, never over raw bytes: ``Path.write_text``
        translates ``\\n`` to the platform's line separator (CRLF on Windows),
        so byte-level hashing would report every chapter of a Windows-hosted
        install as foreign.

        A caller that already holds the text should hash it with
        ``text_digest`` instead of asking for another read: a routine sync
        verifies every chapter of a book, and reading each file twice was half
        of that work (see ``SyncService._chapter_file_is_ours``).
        """
        try:
            markdown = Path(content_path).read_text(encoding="utf-8")
        except Exception:
            return None
        return text_digest(markdown)
