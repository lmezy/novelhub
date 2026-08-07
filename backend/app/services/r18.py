"""R18 classification helpers."""

from typing import Iterable


R18_KEYWORDS = (
    "r18", "18禁", "18+", "成人", "色情", "情色", "限制级",
    "nsfw", "h文", "h向", "成人向", "未满18",
)


def detect_r18(
    *,
    source_is_r18: bool = False,
    title: str = "",
    author: str = "",
    description: str = "",
    tags: Iterable[str] | None = None,
    content_text: str = "",
) -> bool:
    if source_is_r18:
        return True
    parts = [str(title or ""), str(author or ""), str(description or ""), str(content_text or "")]
    parts.extend(str(tag) for tag in tags or [])
    combined = " ".join(parts).lower()
    return any(keyword in combined for keyword in R18_KEYWORDS)
