"""R18 visibility helpers shared by API routes."""

from app.models import Book, User


R18_TAGS = {"r18", "all-ages"}


def can_view_r18(user: User) -> bool:
    return user.role in ("admin", "super_admin") or bool(
        getattr(user, "r18_enabled", False)
    )


def can_view_all_ages(user: User) -> bool:
    return user.role in ("admin", "super_admin") or bool(
        getattr(user, "non_r18_enabled", True)
    )


def ensure_book_visible(user: User, book: Book | None) -> bool:
    if book is None:
        return False
    if book.is_r18:
        return can_view_r18(user)
    return can_view_all_ages(user)


def visible_tags(user: User, tag_names: list[str]) -> list[str]:
    if user.role in ("admin", "super_admin"):
        return list(tag_names)
    return [name for name in tag_names if name not in R18_TAGS]
