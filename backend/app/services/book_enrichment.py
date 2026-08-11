"""Heuristic metadata enrichment for local and manual book imports."""

from __future__ import annotations

import re
from typing import Iterable

from app.services.auto_categorize import (
    DEFAULT_CATEGORY_RULES,
    R18_CATEGORY_NAMES,
)
from app.services.local_file_parser import analyze_name_author
from app.services.r18 import detect_r18


_META_TITLE_RE = re.compile(
    r"^(?:书名|小说名|名称|title)\s*[:：]\s*(.+)$",
    re.I,
)
_META_AUTHOR_RE = re.compile(
    r"^(?:作者|著者|author)\s*[:：]\s*(.+)$",
    re.I,
)
_DESCRIPTION_LABEL_RE = re.compile(
    r"^(?:简介|内容简介|文案|作品简介|book description|introduction)\s*[:：]\s*(.+)$",
    re.I,
)
_CHAPTER_HEADING_RE = re.compile(
    r"^(?:#+\s*)?(?:序章|楔子|正文|终章|后记|尾声|番外|"
    r"第\s*\S*\s*(?:章|节|卷|回|话)|"
    r"(?:chapter|section|part|episode)\b)",
    re.I,
)

TAG_KEYWORDS: dict[str, list[str]] = {
    "玄幻": ["玄幻", "异界", "魔法", "斗气", "修真", "修炼"],
    "都市": ["都市", "现代", "校园", "职场", "商战", "现实", "生活"],
    "言情": ["言情", "爱情", "恋爱", "婚恋", "甜宠", "纯爱", "耽美", "百合"],
    "科幻": ["科幻", "星际", "机甲", "未来", "末日", "进化", "变异"],
    "历史": ["历史", "穿越", "古代", "架空", "宫廷", "战争", "三国"],
    "悬疑": ["悬疑", "推理", "侦探", "恐怖", "灵异", "惊悚", "犯罪"],
    "武侠": ["武侠", "江湖", "门派", "武功", "仙侠", "修仙", "剑道"],
    "游戏": ["游戏", "网游", "电竞", "虚拟", "全息", "竞技"],
    "轻小说": ["轻小说", "二次元", "动漫", "同人", "综漫"],
    "奇幻": ["奇幻", "魔幻", "西幻", "领主", "种田", "冒险"],
    "系统": ["系统", "金手指", "面板"],
    "无限流": ["无限流", "副本", "主神"],
    "重生": ["重生", "重来一次"],
    "穿越": ["穿越", "魂穿", "身穿"],
    "种田": ["种田", "经营", "建设", "领地"],
    "甜宠": ["甜宠", "宠妻", "撒糖"],
    "恐怖": ["恐怖", "灵异", "惊悚"],
    "末世": ["末世", "废土", "丧尸"],
    "星际": ["星际", "太空", "星舰"],
    "搞笑": ["搞笑", "轻松", "日常"],
    "黑暗": ["黑暗", "压抑", "虐主"],
    "调教": ["调教", "训诫", "调校"],
    "反差": ["反差", "反差萌", "白丝", "黑丝"],
    "凌辱": ["凌辱", "羞辱", "侮辱"],
    "乱伦": ["乱伦", "母子", "父女", "兄妹", "姐弟"],
    "母系": ["母系", "母亲", "妈妈", "熟女"],
    "肉文": ["肉文", "黄文", "色文", "H文", "肉戏"],
    "露出": ["露出", "暴露"],
    "绿帽": ["绿帽", "NTR", "ntr", "出轨"],
}


def _clean_tags(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        name = str(value or "").strip().lower()
        if name and name not in seen:
            seen.add(name)
            cleaned.append(name)
    return cleaned


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _extract_meta_lines(text: str) -> dict:
    meta: dict[str, str] = {}
    for line in text.splitlines()[:80]:
        stripped = line.strip()
        if not stripped:
            continue
        match = _META_TITLE_RE.search(stripped)
        if match and "title" not in meta:
            meta["title"] = match.group(1).strip()
        match = _META_AUTHOR_RE.search(stripped)
        if match and "author" not in meta:
            meta["author"] = match.group(1).strip()
        if meta.get("title") and meta.get("author"):
            break
    return meta


def _title_from_filename(filename: str | None) -> str:
    if not filename:
        return ""
    title, _author = analyze_name_author(filename)
    return "" if title == "Unknown" else title


def _author_from_filename(filename: str | None) -> str:
    if not filename:
        return ""
    _title, author = analyze_name_author(filename)
    return "" if author == "Unknown" else author


def extract_description(text: str, limit: int = 500) -> str | None:
    """Return the first plausible description paragraph from local text."""
    for line in text.splitlines()[:200]:
        stripped = line.strip()
        if not stripped:
            continue
        match = _DESCRIPTION_LABEL_RE.match(stripped)
        if match:
            return match.group(1).strip()[:limit] or None
        if _META_TITLE_RE.match(stripped) or _META_AUTHOR_RE.match(stripped):
            continue
        if _CHAPTER_HEADING_RE.match(stripped):
            continue
        return stripped[:limit] or None
    return None


def extract_status(text: str) -> str | None:
    """Guess whether a local book is completed or ongoing."""
    sample = text[:20000].lower()
    if any(word in sample for word in ("已完结", "完结", "全本", "全文完")):
        return "completed"
    if any(word in sample for word in ("连载中", "连载", "更新中")):
        return "ongoing"
    return None


def suggest_tags(
    text: str,
    *,
    title: str = "",
    author: str = "",
    description: str = "",
    limit: int = 12,
) -> list[str]:
    """Suggest common novel tags by scanning metadata and chapter text."""
    combined = " ".join([
        str(title or ""),
        str(author or ""),
        str(description or ""),
        str(text or ""),
    ]).lower()
    matched: list[str] = []
    for tag, keywords in TAG_KEYWORDS.items():
        if tag == "其他":
            continue
        if any(keyword.lower() in combined for keyword in keywords):
            matched.append(tag)
        if len(matched) >= limit:
            break
    return matched


def suggest_categories(
    tags: Iterable[str],
    *,
    title: str = "",
    author: str = "",
    description: str = "",
    is_r18: bool = False,
) -> list[str]:
    """Mirror AutoCategorizationService matching for previews."""
    combined = " ".join([
        *[str(tag or "").lower() for tag in tags],
        str(title or "").lower(),
        str(author or "").lower(),
        str(description or "").lower(),
    ])
    matched: list[str] = []
    for name, keywords in DEFAULT_CATEGORY_RULES.items():
        if name in R18_CATEGORY_NAMES and not is_r18:
            continue
        if any(keyword.lower() in combined for keyword in keywords):
            matched.append(name)
    return matched or ["其他"]


def enrich_book_metadata(
    *,
    meta: dict | None = None,
    chapters: Iterable[tuple[str, str]] | None = None,
    filename: str | None = None,
    fallback_title: str = "",
    fallback_author: str = "",
    sample_limit: int = 300_000,
    is_r18: bool | None = None,
) -> dict:
    """Merge explicit metadata with heuristics extracted from local content."""
    meta = dict(meta or {})
    text_parts: list[str] = []
    size = 0
    for title, content in chapters or []:
        chunk = f"{title}\n{content}"
        text_parts.append(chunk)
        size += len(chunk)
        if size >= sample_limit:
            break
    text = "\n\n".join(text_parts)
    line_meta = _extract_meta_lines(text)

    title = str(
        meta.get("title")
        or line_meta.get("title")
        or _title_from_filename(filename)
        or fallback_title
        or ""
    ).strip()
    author = str(
        meta.get("author")
        or line_meta.get("author")
        or _author_from_filename(filename)
        or fallback_author
        or "Unknown"
    ).strip()
    description = str(
        meta.get("description")
        or extract_description(text)
        or ""
    ).strip() or None
    status = str(meta.get("status") or extract_status(text) or "").strip() or None
    tags = _clean_tags([
        *meta.get("tags", []),
        *suggest_tags(
            text,
            title=title,
            author=author,
            description=description,
        ),
    ])
    detected_r18 = _as_bool(meta.get("is_r18", False)) or detect_r18(
        title=title,
        author=author,
        description=description,
        tags=tags,
        content_text=text,
    )
    is_r18 = detected_r18 if is_r18 is None else bool(is_r18)
    categories = suggest_categories(
        tags,
        title=title,
        author=author,
        description=description,
        is_r18=is_r18,
    )
    return {
        "title": title,
        "author": author,
        "description": description,
        "status": status,
        "tags": tags,
        "is_r18": is_r18,
        "categories": categories,
    }


def analyze_book_text(
    text: str,
    filename: str | None = None,
    is_r18: bool | None = None,
) -> dict:
    """Analyze pasted manual-upload text without splitting chapters."""
    return enrich_book_metadata(
        chapters=[("", text)],
        filename=filename,
        fallback_author="未知作者",
        is_r18=is_r18,
    )
