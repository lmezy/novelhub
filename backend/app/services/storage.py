from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re

from app.core.config import settings


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

    def save_chapter_image(self, book_id: str, url: str, data: bytes) -> str:
        """Save one in-content image and return a storage-relative path."""
        directory = self.chapter_images_dir(book_id)
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:16]
        extension = self._image_extension(data)
        path = directory / f"{digest}.{extension}"
        if not path.exists():
            path.write_bytes(data)
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
        path.write_bytes(data)
        return path.relative_to(self.root.parent).as_posix()

    def save_display_cover(self, book_id: str, data: bytes) -> str:
        """Save a user-selected cover without overwriting the source cover."""
        directory = self.cover_dir()
        directory.mkdir(parents=True, exist_ok=True)
        extension = self._image_extension(data)
        safe_id = safe_segment(book_id)
        for old in directory.glob(f"{safe_id}_display.*"):
            old.unlink(missing_ok=True)
        path = directory / f"{safe_id}_display.{extension}"
        path.write_bytes(data)
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
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return metadata_path

    def write_chapter(
        self,
        author: str,
        title: str,
        number: int,
        chapter_title: str,
        content: str,
        ref: BookRef | None = None,
    ) -> tuple[str, str]:
        path = self.book_dir(author, title, ref)
        path.mkdir(parents=True, exist_ok=True)
        file_path = path / f"{number:06d}.md"
        markdown = f"#{chapter_title}\n\n{content.strip()}\n"
        file_path.write_text(markdown, encoding="utf-8")
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
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
        """
        try:
            markdown = Path(content_path).read_text(encoding="utf-8")
        except Exception:
            return None
        return hashlib.sha256(markdown.encode("utf-8")).hexdigest()
