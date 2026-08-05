"""Parse local book files (txt/epub/docx/rtf) without importing them to the DB."""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

SUPPORTED_EXTENSIONS = {".txt", ".epub", ".docx", ".doc", ".md"}

_CHAPTER_RE = re.compile(
    r"^\s{0,4}(?:"
    r"(?:序章|楔子|正文(?!完结)|终章|后记|尾声|番外|"
    r"第\s{0,4}[0-9〇零一二三四五六七八九十百千万两]{1,10}\s{0,4}(?:章|节|卷|回|话)(?!说))"
    r"|\d{1,5}\s*[、.．:：，,．]\s*\S{1,30}"
    r"|(?:[Cc]hapter|[Ss]ection|[Pp]art|[Nn][Oo]\.?|[Ee]pisode)\s{0,4}\d{1,4}\s*\S{0,30}"
    r"|第[0-9〇零一二三四五六七八九十百千万两]{1,10}章\s*\S{0,30}"
    r")$",
    re.MULTILINE,
)

_META_PATTERNS = [
    re.compile(r"(?:书名|小说名|名称)\s*[:：]\s*(.+)"),
    re.compile(r"(?:作者|著者|author)\s*[:：]\s*(.+)", re.I),
]


def is_supported_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def analyze_name_author(filename: str) -> tuple[str, str]:
    """Split a book filename into title and author, matching common local files."""
    stem = Path(filename).stem.strip()
    if not stem:
        return filename, "Unknown"

    patterns = [
        re.compile(r"《(.+?)》\s*(?:作者[:：]\s*(.+))?", re.S),
        re.compile(r"^(.+?)\s+作者[:：]\s*(.+)$"),
        re.compile(r"^(.+?)\s+by\s+(.+)$", re.I),
        re.compile(r"^(.+?)\s*[-_]\s*(.+)$"),
    ]
    for pattern in patterns:
        match = pattern.search(stem)
        if match:
            title = (match.group(1) or "").strip()
            author = (match.group(2) or "").strip()
            if title:
                return title, author or "Unknown"
    return stem, "Unknown"


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_text_metadata(text: str) -> dict:
    meta: dict = {}
    for line in text.splitlines()[:60]:
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in _META_PATTERNS:
            match = pattern.search(stripped)
            if match:
                key = "author" if "author" in pattern.pattern.lower() or "作者" in stripped else "title"
                meta[key] = match.group(1).strip()
                break
        if meta.get("title") and meta.get("author"):
            break
    return meta


def split_text_chapters(text: str) -> list[tuple[str, str]]:
    """Split plain text into (title, content) chapters."""
    lines = text.splitlines()
    chapters: list[tuple[str, str]] = []
    current_title = ""
    current: list[str] = []

    def flush() -> None:
        nonlocal current_title, current
        content = "\n".join(current).strip()
        if current_title or content:
            chapters.append((current_title or "正文", content))
        current_title = ""
        current = []

    for line in lines:
        if _CHAPTER_RE.match(line):
            flush()
            current_title = line.strip()
            current.append(line.strip())
        else:
            current.append(line)
    flush()

    if not chapters:
        chunks = [text[i:i + 8000] for i in range(0, len(text), 8000)]
        chapters = [
            (f"第{index}节", chunk.strip())
            for index, chunk in enumerate(chunks, start=1)
            if chunk.strip()
        ]
    return chapters


def parse_txt(path: Path) -> tuple[dict, list[tuple[str, str]]]:
    raw = path.read_bytes()
    text = _decode_text(raw)
    title, author = analyze_name_author(path.name)
    meta = _extract_text_metadata(text)
    if not re.search(r"\d", path.stem) and meta.get("title"):
        title = meta["title"]
    if meta.get("author"):
        author = meta["author"]
    chapters = split_text_chapters(text)
    if not chapters:
        raise ValueError(f"No chapter content found: {path}")
    return {"title": title, "author": author}, chapters


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def parse_epub(path: Path) -> tuple[dict, list[tuple[str, str]]]:
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(str(path))
    title = ""
    author = ""
    for item in book.get_metadata("DC", "title"):
        if item and item[0]:
            title = str(item[0])
            break
    for item in book.get_metadata("DC", "creator"):
        if item and item[0]:
            author = str(item[0])
            break
    if not title:
        title, _ = analyze_name_author(path.name)

    chapters: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        name = item.get_name() or item.get_id() or str(len(chapters))
        if name in seen:
            continue
        seen.add(name)
        raw = item.get_content()
        if isinstance(raw, str):
            html = raw
        else:
            try:
                html = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
        text = _html_to_text(html)
        if not text.strip():
            continue
        head = re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", html, re.I | re.S)
        chapter_title = BeautifulSoup(head.group(1), "lxml").get_text(" ", strip=True) if head else ""
        if not chapter_title:
            first_line = text.splitlines()[0].strip()
            chapter_title = first_line[:60] if first_line else Path(name).stem
        chapters.append((chapter_title, text))

    if not chapters:
        raise ValueError(f"No readable chapters found in epub: {path}")
    return {"title": title, "author": author or "Unknown"}, chapters


def _strip_rtf(text: str) -> str:
    # Minimal RTF text extraction for .doc files that are actually RTF.
    text = re.sub(r"\{\\\*?[^{}]*\}", "", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = re.sub(r"\{\s*|\s*\}", "", text)
    text = re.sub(r"\\'[0-9a-fA-F]{2}", "", text)
    return text.strip()


def parse_docx(path: Path) -> tuple[dict, list[tuple[str, str]]]:
    with zipfile.ZipFile(path) as zf:
        core_xml = ""
        if "docProps/core.xml" in zf.namelist():
            core_xml = zf.read("docProps/core.xml").decode("utf-8", errors="replace")
        document_xml = zf.read("word/document.xml").decode("utf-8", errors="replace")

    title = ""
    author = ""
    if core_xml:
        title_match = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", core_xml, re.S)
        author_match = re.search(r"<dc:creator[^>]*>(.*?)</dc:creator>", core_xml, re.S)
        if title_match:
            title = title_match.group(1).strip()
        if author_match:
            author = author_match.group(1).strip()
    if not title:
        title, _ = analyze_name_author(path.name)

    soup = BeautifulSoup(document_xml, "lxml")
    paragraphs = []
    for para in soup.find_all("w:p"):
        text = "".join(node.get_text("", strip=True) for node in para.find_all("w:t"))
        if text.strip():
            paragraphs.append(text)
    text = "\n".join(paragraphs)
    chapters = split_text_chapters(text)
    if not chapters:
        raise ValueError(f"No readable text found in docx: {path}")
    return {"title": title, "author": author or "Unknown"}, chapters


def parse_doc(path: Path) -> tuple[dict, list[tuple[str, str]]]:
    raw = path.read_bytes()
    if raw.lstrip().startswith(b"{\\rtf"):
        text = _strip_rtf(raw.decode("utf-8", errors="replace"))
        title, author = analyze_name_author(path.name)
        chapters = split_text_chapters(text)
        if not chapters:
            raise ValueError(f"No readable text found in doc: {path}")
        return {"title": title, "author": author}, chapters
    raise ValueError(
        f"Legacy binary .doc is not supported yet: {path}. "
        "Convert it to .docx or .txt and retry."
    )


def parse_local_file(path: Path) -> tuple[dict, list[tuple[str, str]]]:
    """Return metadata and chapter content for a supported local book file."""
    if not is_supported_file(path):
        raise ValueError(f"Unsupported local file: {path}")
    ext = path.suffix.lower()
    if ext == ".txt":
        return parse_txt(path)
    if ext == ".epub":
        return parse_epub(path)
    if ext == ".docx":
        return parse_docx(path)
    if ext == ".doc":
        return parse_doc(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    title, author = analyze_name_author(path.name)
    chapters = split_text_chapters(text)
    return {"title": title, "author": author}, chapters
