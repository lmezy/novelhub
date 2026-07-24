from pathlib import Path

from app.crawler.base import RemoteBook, RemoteChapter


class LocalMarkdownPlugin:
    name = "local_markdown"

    async def fetch_book(self, url: str) -> RemoteBook:
        root = Path(url.replace("file://", "")).resolve()
        metadata_path = root / "metadata.json"
        metadata = {}
        if metadata_path.exists():
            import json

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        chapter_paths = sorted(root.glob("*.md"))
        chapters = [
            RemoteChapter(
                source_chapter_id=path.stem,
                title=self._title_from_markdown(path),
                url=str(path),
                chapter_number=index,
            )
            for index, path in enumerate(chapter_paths, start=1)
        ]
        return RemoteBook(
            source_book_id=metadata.get("source_book_id", root.name),
            title=metadata.get("title", root.name),
            author=metadata.get("author", root.parent.name),
            description=metadata.get("description"),
            status=metadata.get("status", "unknown"),
            chapters=chapters,
        )

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        return Path(chapter.url).read_text(encoding="utf-8")

    def _title_from_markdown(self, path: Path) -> str:
        first_line = path.read_text(encoding="utf-8").splitlines()[0:1]
        if first_line and first_line[0].startswith("#"):
            return first_line[0].lstrip("#").strip()
        return path.stem
