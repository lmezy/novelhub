"""User custom tags on books, with public/private and contributor visibility."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, BookCustomTag, CustomTag, User
from app.services.visibility import ensure_book_visible


def _tag_dict(tag: CustomTag) -> dict:
    return {
        "id": tag.id,
        "name": tag.name,
        "is_public": bool(tag.is_public),
        "show_user": bool(tag.show_user),
        "count": 0,
        "applied_by_me": False,
        "users": [],
    }


async def list_book_custom_tags_map(
    db: AsyncSession,
    book_ids: list[str],
    user: User,
) -> dict[str, list[dict]]:
    """Return visible custom tags grouped by book for a user."""
    book_ids = list(dict.fromkeys(book_ids))
    if not book_ids:
        return {}

    rows = (
        await db.execute(
            select(BookCustomTag, CustomTag, User)
            .join(CustomTag, BookCustomTag.custom_tag_id == CustomTag.id)
            .join(User, BookCustomTag.user_id == User.id)
            .where(
                BookCustomTag.book_id.in_(book_ids),
                or_(
                    CustomTag.is_public.is_(True),
                    CustomTag.owner_id == user.id,
                ),
            )
            .order_by(CustomTag.name)
        )
    ).all()

    result: dict[str, dict[str, dict]] = {}
    for bct, tag, tagger in rows:
        book_id = bct.book_id
        bucket = result.setdefault(book_id, {})
        item = bucket.get(tag.id)
        if item is None:
            item = _tag_dict(tag)
            bucket[tag.id] = item
        item["count"] += 1
        if tagger.id == user.id:
            item["applied_by_me"] = True
        if not tag.is_public or tag.show_user:
            item["users"].append({"id": tagger.id, "username": tagger.username})

    output: dict[str, list[dict]] = {}
    for book_id, bucket in result.items():
        tags = list(bucket.values())
        tags.sort(key=lambda item: (-item["count"], item["name"].lower()))
        output[book_id] = tags
    return output


async def list_book_custom_tags(
    db: AsyncSession,
    book_id: str,
    user: User,
) -> list[dict]:
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise ValueError("Book not found")
    return (await list_book_custom_tags_map(db, [book_id], user)).get(book_id, [])


async def apply_custom_tag(
    db: AsyncSession,
    book_id: str,
    user: User,
    name: str,
    is_public: bool = False,
    show_user: bool = True,
) -> dict:
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise ValueError("Book not found")

    name = (name or "").strip()[:50]
    if not name:
        raise ValueError("Tag name is required")

    public_tag = await db.scalar(
        select(CustomTag)
        .where(
            CustomTag.name == name,
            CustomTag.is_public.is_(True),
        )
        .order_by(CustomTag.created_at)
    )
    if public_tag is not None:
        tag = public_tag
    else:
        mine = await db.scalar(
            select(CustomTag).where(
                CustomTag.owner_id == user.id,
                CustomTag.name == name,
            )
        )
        if mine is not None:
            tag = mine
            if (
                bool(tag.is_public) != bool(is_public)
                or bool(tag.show_user) != bool(show_user)
            ):
                other_public = await db.scalar(
                    select(CustomTag).where(
                        CustomTag.name == name,
                        CustomTag.is_public.is_(True),
                        CustomTag.id != tag.id,
                    )
                )
                if is_public and other_public is not None:
                    raise ValueError("A public tag with this name already exists")
                if not is_public and tag.is_public:
                    other_users = await db.scalar(
                        select(func.count())
                        .select_from(BookCustomTag)
                        .where(
                            BookCustomTag.custom_tag_id == tag.id,
                            BookCustomTag.user_id != user.id,
                        )
                    )
                    if other_users:
                        raise ValueError("A shared public tag cannot be made private")
                tag.is_public = bool(is_public)
                tag.show_user = bool(show_user)
        else:
            tag = CustomTag(
                id=str(uuid4()),
                owner_id=user.id,
                name=name,
                is_public=bool(is_public),
                show_user=bool(show_user),
            )
            db.add(tag)
            await db.flush()

    existing = await db.scalar(
        select(BookCustomTag).where(
            BookCustomTag.book_id == book_id,
            BookCustomTag.custom_tag_id == tag.id,
            BookCustomTag.user_id == user.id,
        )
    )
    if existing is None:
        db.add(
            BookCustomTag(
                book_id=book_id,
                custom_tag_id=tag.id,
                user_id=user.id,
            )
        )
    await db.commit()
    return (await list_book_custom_tags_map(db, [book_id], user)).get(book_id, [])


async def remove_custom_tag_application(
    db: AsyncSession,
    book_id: str,
    tag_id: str,
    user: User,
) -> list[dict]:
    application = await db.scalar(
        select(BookCustomTag).where(
            BookCustomTag.book_id == book_id,
            BookCustomTag.custom_tag_id == tag_id,
            BookCustomTag.user_id == user.id,
        )
    )
    if application is None:
        raise ValueError("Tag application not found")

    await db.delete(application)
    remaining = await db.scalar(
        select(func.count())
        .select_from(BookCustomTag)
        .where(BookCustomTag.custom_tag_id == tag_id)
    )
    if remaining == 0:
        tag = await db.get(CustomTag, tag_id)
        if tag is not None and tag.owner_id == user.id:
            await db.delete(tag)
    await db.commit()
    return (await list_book_custom_tags_map(db, [book_id], user)).get(book_id, [])


async def update_custom_tag(
    db: AsyncSession,
    tag_id: str,
    user: User,
    is_public: bool | None = None,
    show_user: bool | None = None,
) -> dict:
    tag = await db.get(CustomTag, tag_id)
    if tag is None:
        raise ValueError("Tag not found")
    if tag.owner_id != user.id and user.role not in ("admin", "super_admin"):
        raise PermissionError("Only the tag owner can edit it")

    if is_public is not None:
        if is_public and not tag.is_public:
            other_public = await db.scalar(
                select(CustomTag).where(
                    CustomTag.name == tag.name,
                    CustomTag.is_public.is_(True),
                    CustomTag.id != tag.id,
                )
            )
            if other_public is not None:
                raise ValueError("A public tag with this name already exists")
        if not is_public and tag.is_public:
            other_users = await db.scalar(
                select(func.count())
                .select_from(BookCustomTag)
                .where(
                    BookCustomTag.custom_tag_id == tag.id,
                    BookCustomTag.user_id != tag.owner_id,
                )
            )
            if other_users:
                raise ValueError("A shared public tag cannot be made private")
        tag.is_public = bool(is_public)
    if show_user is not None:
        tag.show_user = bool(show_user)

    await db.commit()
    return {
        "id": tag.id,
        "name": tag.name,
        "is_public": bool(tag.is_public),
        "show_user": bool(tag.show_user),
    }


async def delete_custom_tag(db: AsyncSession, tag_id: str, user: User) -> None:
    tag = await db.get(CustomTag, tag_id)
    if tag is None:
        raise ValueError("Tag not found")
    if tag.owner_id != user.id and user.role not in ("admin", "super_admin"):
        raise PermissionError("Only the tag owner can delete it")
    await db.delete(tag)
    await db.commit()
