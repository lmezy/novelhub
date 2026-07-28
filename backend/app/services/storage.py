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
