"""Local library directory scanning and direct reading helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.core.config import settings
from app.services.local_file_parser import (
    is_supported_file,
    parse_local_file,
)
from app.services.book_enrichment import enrich_book_metadata


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
        enriched = enrich_book_metadata(
            meta=meta,
            chapters=chapters,
            filename=book_dir.name,
            fallback_title=book_dir.stem,
            fallback_author="Unknown",
        )
        return {
            "path": str(book_dir),
            "title": enriched["title"],
            "author": enriched["author"],
            "description": enriched["description"],
            "status": enriched["status"],
            "tags": enriched["tags"],
            "is_r18": enriched["is_r18"],
            "categories": enriched["categories"],
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
    content_pairs: list[tuple[str, str]] = []
    sample_size = 0
    for index, chapter_path in enumerate(chapters, start=1):
        try:
            content = chapter_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
        if sample_size < 300_000 and content:
            content_pairs.append((_chapter_title(chapter_path), content))
            sample_size += len(content)
        chapter_refs.append({
            "title": _chapter_title(chapter_path),
            "path": str(chapter_path),
            "chapter_number": index,
        })

    enriched = enrich_book_metadata(
        meta=meta,
        chapters=content_pairs,
        filename=book_dir.name,
        fallback_title=book_dir.name,
        fallback_author=book_dir.parent.name,
    )
    return {
        "path": str(book_dir),
        "title": enriched["title"],
        "author": enriched["author"],
        "description": enriched["description"],
        "status": enriched["status"],
        "tags": enriched["tags"],
        "is_r18": enriched["is_r18"],
        "categories": enriched["categories"],
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
                "is_r18": info["is_r18"],
                "categories": info["categories"],
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
                "is_r18": info["is_r18"],
                "categories": info["categories"],
                "chapter_count": info["chapter_count"],
                "has_metadata": info["has_metadata"],
                "format": info.get("format", "markdown"),
            })
            added_paths.add(key)

    return books


def list_local_import_roots() -> list[dict]:
    """Return configured, currently visible import roots."""
    roots: list[dict] = []
    for raw in (settings.LOCAL_IMPORT_ROOTS or "").split(","):
        value = (raw or "").strip()
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            roots.append({
                "path": str(path),
                "name": path.name or str(path),
            })
    return roots


def list_local_directories(path: str) -> dict:
    """List subdirectories under one of the configured import roots."""
    target = Path(path).expanduser().resolve()
    roots = [
        Path(value).expanduser().resolve()
        for value in (settings.LOCAL_IMPORT_ROOTS or "").split(",")
        if (value or "").strip()
    ]
    if not any(target == root or root in target.parents for root in roots):
        raise ValueError(f"Path is outside configured import roots: {path}")
    if not target.is_dir():
        raise ValueError(f"Not a directory: {path}")

    directories = []
    for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
        if child.is_dir():
            directories.append({
                "path": str(child),
                "name": child.name,
            })

    return {
        "path": str(target),
        "name": target.name or str(target),
        "parent": str(target.parent) if target not in roots else None,
        "directories": directories,
    }
