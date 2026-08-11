from pathlib import Path
import hashlib
import json
import re

from app.core.config import settings


def safe_segment(value: str) -> str:
    normalized = re.sub(r'[\\/:*?"<>|]+', "_", value).strip()
    return normalized or "unknown"


class BookStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.STORAGE_PATH)

    def book_dir(self, author: str, title: str) -> Path:
        return self.root / safe_segment(author) / safe_segment(title)

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

    def write_metadata(self, author: str, title: str, metadata: dict) -> Path:
        path = self.book_dir(author, title)
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
    ) -> tuple[str, str]:
        path = self.book_dir(author, title)
        path.mkdir(parents=True, exist_ok=True)
        file_path = path / f"{number:06d}.md"
        markdown = f"#{chapter_title}\n\n{content.strip()}\n"
        file_path.write_text(markdown, encoding="utf-8")
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        return str(file_path), content_hash

    def read_chapter(self, content_path: str) -> str:
        return Path(content_path).read_text(encoding="utf-8")
