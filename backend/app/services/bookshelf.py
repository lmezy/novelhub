"""User bookshelf group helpers, modeled after Legado's bookshelf groups."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BookFavorite, BookFavoriteGroup, BookshelfGroup, User


async def _favorite_for_user(
    db: AsyncSession,
    user: User,
    book_id: str,
) -> BookFavorite:
    favorite = await db.scalar(
        select(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book_id,
        )
    )
    if favorite is None:
        raise ValueError("Book is not in your bookshelf")
    return favorite


async def list_user_groups(db: AsyncSession, user: User) -> list[dict]:
    rows = (
        await db.execute(
            select(BookshelfGroup, func.count(BookFavoriteGroup.favorite_id))
            .outerjoin(
                BookFavoriteGroup,
                BookFavoriteGroup.group_id == BookshelfGroup.id,
            )
            .where(BookshelfGroup.user_id == user.id)
            .group_by(BookshelfGroup.id)
            .order_by(BookshelfGroup.order, BookshelfGroup.created_at)
        )
    ).all()
    return [
        {
            "id": group.id,
            "name": group.name,
            "order": group.order,
            "show": bool(group.show),
            "count": count or 0,
        }
        for group, count in rows
    ]


async def create_user_group(
    db: AsyncSession,
    user: User,
    name: str,
    show: bool = True,
) -> dict:
    name = (name or "").strip()[:50]
    if not name:
        raise ValueError("Group name is required")
    existing = await db.scalar(
        select(BookshelfGroup).where(
            BookshelfGroup.user_id == user.id,
            BookshelfGroup.name == name,
        )
    )
    if existing is not None:
        raise ValueError("A group with this name already exists")

    max_order = await db.scalar(
        select(func.coalesce(func.max(BookshelfGroup.order), 0)).where(
            BookshelfGroup.user_id == user.id
        )
    )
    group = BookshelfGroup(
        id=str(uuid4()),
        user_id=user.id,
        name=name,
        order=int(max_order or 0) + 1,
        show=bool(show),
    )
    db.add(group)
    await db.commit()
    return {
        "id": group.id,
        "name": group.name,
        "order": group.order,
        "show": bool(group.show),
        "count": 0,
    }


async def update_user_group(
    db: AsyncSession,
    user: User,
    group_id: str,
    name: str | None = None,
    show: bool | None = None,
) -> dict:
    group = await db.get(BookshelfGroup, group_id)
    if group is None or group.user_id != user.id:
        raise ValueError("Group not found")

    if name is not None:
        name = name.strip()[:50]
        if not name:
            raise ValueError("Group name is required")
        duplicate = await db.scalar(
            select(BookshelfGroup).where(
                BookshelfGroup.user_id == user.id,
                BookshelfGroup.name == name,
                BookshelfGroup.id != group.id,
            )
        )
        if duplicate is not None:
            raise ValueError("A group with this name already exists")
        group.name = name
    if show is not None:
        group.show = bool(show)

    await db.commit()
    count = await db.scalar(
        select(func.count())
        .select_from(BookFavoriteGroup)
        .where(BookFavoriteGroup.group_id == group.id)
    )
    return {
        "id": group.id,
        "name": group.name,
        "order": group.order,
        "show": bool(group.show),
        "count": count or 0,
    }


async def delete_user_group(db: AsyncSession, user: User, group_id: str) -> None:
    group = await db.get(BookshelfGroup, group_id)
    if group is None or group.user_id != user.id:
        raise ValueError("Group not found")
    await db.execute(
        delete(BookFavoriteGroup).where(BookFavoriteGroup.group_id == group.id)
    )
    await db.delete(group)
    await db.commit()


async def favorite_group_ids_by_book(
    db: AsyncSession,
    user_id: str,
    book_ids: list[str],
) -> dict[str, list[str]]:
    book_ids = list(dict.fromkeys(book_ids))
    if not book_ids:
        return {}
    rows = (
        await db.execute(
            select(BookFavorite.id, BookFavorite.book_id, BookFavoriteGroup.group_id)
            .outerjoin(
                BookFavoriteGroup,
                BookFavoriteGroup.favorite_id == BookFavorite.id,
            )
            .where(
                BookFavorite.user_id == user_id,
                BookFavorite.book_id.in_(book_ids),
            )
        )
    ).all()
    result: dict[str, list[str]] = {book_id: [] for book_id in book_ids}
    for favorite_id, book_id, group_id in rows:
        if group_id is not None:
            result[book_id].append(group_id)
    return result


async def book_group_ids(db: AsyncSession, user: User, book_id: str) -> list[str]:
    favorite = await _favorite_for_user(db, user, book_id)
    rows = await db.scalars(
        select(BookFavoriteGroup.group_id).where(
            BookFavoriteGroup.favorite_id == favorite.id
        )
    )
    return list(rows)


async def set_book_groups(
    db: AsyncSession,
    user: User,
    book_id: str,
    group_ids: list[str],
) -> list[str]:
    favorite = await _favorite_for_user(db, user, book_id)
    group_ids = list(dict.fromkeys(group_ids or []))
    if group_ids:
        groups = await db.scalars(
            select(BookshelfGroup).where(
                BookshelfGroup.user_id == user.id,
                BookshelfGroup.id.in_(group_ids),
            )
        )
        valid_ids = {g.id for g in groups}
        if valid_ids != set(group_ids):
            raise ValueError("Some groups do not exist")

    await db.execute(
        delete(BookFavoriteGroup).where(
            BookFavoriteGroup.favorite_id == favorite.id
        )
    )
    for group_id in group_ids:
        db.add(BookFavoriteGroup(favorite_id=favorite.id, group_id=group_id))
    await db.commit()
    return group_ids


async def set_books_groups(
    db: AsyncSession,
    user: User,
    book_ids: list[str],
    group_ids: list[str],
) -> int:
    book_ids = list(dict.fromkeys(book_ids or []))
    if not book_ids:
        return 0
    group_ids = list(dict.fromkeys(group_ids or []))
    if group_ids:
        groups = await db.scalars(
            select(BookshelfGroup).where(
                BookshelfGroup.user_id == user.id,
                BookshelfGroup.id.in_(group_ids),
            )
        )
        valid_ids = {g.id for g in groups}
        if valid_ids != set(group_ids):
            raise ValueError("Some groups do not exist")

    favorites = (
        await db.scalars(
            select(BookFavorite).where(
                BookFavorite.user_id == user.id,
                BookFavorite.book_id.in_(book_ids),
            )
        )
    ).all()
    updated = 0
    for favorite in favorites:
        await db.execute(
            delete(BookFavoriteGroup).where(
                BookFavoriteGroup.favorite_id == favorite.id
            )
        )
        for group_id in group_ids:
            db.add(BookFavoriteGroup(favorite_id=favorite.id, group_id=group_id))
        updated += 1
    await db.commit()
    return updated
