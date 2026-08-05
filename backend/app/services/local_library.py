"""Local library directory scanning and direct reading helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.services.local_file_parser import (
    is_supported_file,
    parse_local_file,
)


def _read_metadata(book_dir: Path) -> dict:
    meta_path = book_dir / "metadata.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _chapter_title(chapter_path: Path) -> str:
    try:
        first_line = chapter_path.read_text(encoding="utf-8").splitlines()[0] or ""
    except OSError:
        first_line = ""
    return first_line.lstrip("#").strip() or chapter_path.stem


def _chapter_files(book_dir: Path) -> list[Path]:
    numbered = sorted(book_dir.glob("[0-9]*.md"))
    return numbered or sorted(book_dir.glob("*.md"))


def looks_like_book_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "metadata.json").is_file():
        return True
    return any(path.glob("*.md"))


def parse_local_book(path: str | Path) -> dict:
    """Parse one local book directory without touching the database."""
    book_dir = Path(path).expanduser().resolve()
    if book_dir.is_file():
        meta, chapters = parse_local_file(book_dir)
        chapter_refs = [
            {
                "title": title,
                "path": str(book_dir),
                "chapter_number": index,
            }
            for index, (title, _content) in enumerate(chapters, start=1)
        ]
        return {
            "path": str(book_dir),
            "title": str(meta.get("title") or book_dir.stem),
            "author": str(meta.get("author") or "Unknown"),
            "description": None,
            "status": None,
            "tags": [],
            "chapter_count": len(chapter_refs),
            "has_metadata": False,
            "format": book_dir.suffix.lower().lstrip("."),
            "chapters": chapter_refs,
        }
    if not book_dir.is_dir():
        raise ValueError(f"Not a directory: {path}")

    meta = _read_metadata(book_dir)
    chapters = _chapter_files(book_dir)
    if not chapters and not meta:
        raise ValueError(f"No markdown chapters found: {path}")

    chapter_refs = []
    for index, chapter_path in enumerate(chapters, start=1):
        chapter_refs.append({
            "title": _chapter_title(chapter_path),
            "path": str(chapter_path),
            "chapter_number": index,
        })

    return {
        "path": str(book_dir),
        "title": str(meta.get("title") or book_dir.name),
        "author": str(meta.get("author") or book_dir.parent.name),
        "description": meta.get("description"),
        "status": meta.get("status"),
        "tags": list(meta.get("tags") or []),
        "chapter_count": len(chapter_refs),
        "has_metadata": bool(meta),
        "format": "markdown",
        "chapters": chapter_refs,
    }


def scan_local_library(root: str, max_depth: int = 3) -> list[dict]:
    """Scan a directory tree and return candidate local book folders."""
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError(f"Not a directory: {root}")

    max_depth = max(1, int(max_depth or 3))
    books: list[dict] = []
    added_paths: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root_path):
        current = Path(dirpath)
        rel = current.relative_to(root_path)
        depth = 0 if rel == Path(".") else len(rel.parts)
        if depth > max_depth:
            dirnames[:] = []
            continue
        root_book_ok = depth > 0 or (current / "metadata.json").is_file()
        if root_book_ok and looks_like_book_dir(current):
            try:
                info = parse_local_book(current)
            except ValueError:
                dirnames[:] = []
                continue
            if info["chapter_count"] == 0:
                dirnames[:] = []
                continue
            books.append({
                "path": info["path"],
                "title": info["title"],
                "author": info["author"],
                "description": info["description"],
                "status": info["status"],
                "tags": info["tags"],
                "chapter_count": info["chapter_count"],
                "has_metadata": info["has_metadata"],
                "format": info.get("format", "markdown"),
            })
            added_paths.add(info["path"])
            dirnames[:] = []
            continue

        for filename in filenames:
            file_path = current / filename
            if not is_supported_file(file_path):
                continue
            if file_path.suffix.lower() == ".md":
                continue
            key = str(file_path)
            if key in added_paths:
                continue
            try:
                info = parse_local_book(file_path)
            except ValueError:
                continue
            if info["chapter_count"] == 0:
                continue
            books.append({
                "path": info["path"],
                "title": info["title"],
                "author": info["author"],
                "description": info["description"],
                "status": info["status"],
                "tags": info["tags"],
                "chapter_count": info["chapter_count"],
                "has_metadata": info["has_metadata"],
                "format": info.get("format", "markdown"),
            })
            added_paths.add(key)

    return books
