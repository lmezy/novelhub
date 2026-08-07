"""R18 visibility helpers shared by API routes."""

from app.models import Book, User


CLASSIFICATION_TAGS = {"r18", "all-ages"}
R18_TAGS = {
    "调教",
    "反差",
    "凌辱",
    "乱伦",
    "母系",
    "肉文",
    "露出",
    "绿帽",
    "ntr",
    "NTR",
    "色情",
    "黄文",
    "色文",
    "h文",
    "H文",
    "肉戏",
    "sm",
    "SM",
    "羞辱",
    "侮辱",
    "熟女",
    "出轨",
}


def can_view_r18(user: User) -> bool:
    return bool(getattr(user, "r18_enabled", False))


def can_view_all_ages(user: User) -> bool:
    return bool(getattr(user, "non_r18_enabled", True))


def ensure_book_visible(user: User, book: Book | None) -> bool:
    if book is None:
        return False
    if (
        book.owner_id
        and not book.is_public
        and user.role not in ("admin", "super_admin")
        and book.owner_id != user.id
    ):
        return False
    if book.is_r18:
        return can_view_r18(user)
    return can_view_all_ages(user)


def visible_tags(user: User, tag_names: list[str]) -> list[str]:
    if user.role in ("admin", "super_admin"):
        visible = list(tag_names)
    else:
        visible = [name for name in tag_names if name not in CLASSIFICATION_TAGS]
    if not can_view_r18(user):
        visible = [name for name in visible if name not in R18_TAGS]
    return visible
