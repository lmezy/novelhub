"""Book deletion helpers shared by single, batch, and source cleanup paths."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Book,
    BookCategory,
    BookCustomTag,
    BookFavorite,
    BookFavoriteGroup,
    BookTag,
    BookVersion,
    Chapter,
    ChapterEmbedding,
    ReadingProgress,
)
from app.services.search import search_service

_CHUNK_SIZE = 500


def _chunks(values: list[str], size: int = _CHUNK_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


async def delete_books(
    db: AsyncSession,
    book_ids: list[str],
    *,
    commit: bool = True,
) -> int:
    """Remove all database rows that reference the given books."""
    if not book_ids:
        return 0

    unique_ids = list(dict.fromkeys(book_ids))
    chapter_ids: list[str] = []
    for chunk in _chunks(unique_ids):
        chapter_ids.extend(
            list(
                await db.scalars(
                    select(Chapter.id).where(Chapter.book_id.in_(chunk))
                )
            )
        )
    chapter_ids = list(dict.fromkeys(chapter_ids))

    favorite_ids: list[str] = []
    for chunk in _chunks(unique_ids):
        favorite_ids.extend(
            list(
                await db.scalars(
                    select(BookFavorite.id).where(BookFavorite.book_id.in_(chunk))
                )
            )
        )
    favorite_ids = list(dict.fromkeys(favorite_ids))

    for chunk in _chunks(favorite_ids):
        await db.execute(
            delete(BookFavoriteGroup).where(BookFavoriteGroup.favorite_id.in_(chunk))
        )

    for chunk in _chunks(chapter_ids):
        await db.execute(
            delete(BookVersion).where(BookVersion.chapter_id.in_(chunk))
        )
        await db.execute(
            delete(ChapterEmbedding).where(ChapterEmbedding.chapter_id.in_(chunk))
        )
        await db.execute(
            delete(ReadingProgress).where(ReadingProgress.chapter_id.in_(chunk))
        )

    for chunk in _chunks(unique_ids):
        await db.execute(
            delete(ChapterEmbedding).where(ChapterEmbedding.book_id.in_(chunk))
        )
        await db.execute(
            delete(ReadingProgress).where(ReadingProgress.book_id.in_(chunk))
        )
        await db.execute(delete(Chapter).where(Chapter.book_id.in_(chunk)))
        await db.execute(delete(BookTag).where(BookTag.book_id.in_(chunk)))
        await db.execute(delete(BookCategory).where(BookCategory.book_id.in_(chunk)))
        await db.execute(delete(BookCustomTag).where(BookCustomTag.book_id.in_(chunk)))
        await db.execute(delete(BookFavorite).where(BookFavorite.book_id.in_(chunk)))
        await db.execute(delete(Book).where(Book.id.in_(chunk)))

    if commit:
        await db.commit()

    if chapter_ids:
        search_service.delete_chapters_from_index(chapter_ids)
    if unique_ids:
        search_service.delete_books_from_index(unique_ids)

    return len(unique_ids)
