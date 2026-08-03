"""Book deletion helpers shared by single, batch, and source cleanup paths."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Book,
    BookCategory,
    BookTag,
    BookVersion,
    Chapter,
    ChapterEmbedding,
    ReadingProgress,
)
from app.services.search import search_service


async def delete_books(
    db: AsyncSession,
    book_ids: list[str],
    *,
    commit: bool = True,
) -> int:
    """Remove all database rows that reference the given books."""
    if not book_ids:
        return 0

    chapter_ids = list(
        await db.scalars(
            select(Chapter.id).where(Chapter.book_id.in_(book_ids))
        )
    )

    if chapter_ids:
        await db.execute(
            delete(BookVersion).where(BookVersion.chapter_id.in_(chapter_ids))
        )
        await db.execute(
            delete(ChapterEmbedding).where(ChapterEmbedding.chapter_id.in_(chapter_ids))
        )

    await db.execute(
        delete(ChapterEmbedding).where(ChapterEmbedding.book_id.in_(book_ids))
    )
    await db.execute(delete(Chapter).where(Chapter.book_id.in_(book_ids)))
    await db.execute(delete(BookTag).where(BookTag.book_id.in_(book_ids)))
    await db.execute(delete(BookCategory).where(BookCategory.book_id.in_(book_ids)))
    await db.execute(
        delete(ReadingProgress).where(ReadingProgress.book_id.in_(book_ids))
    )
    await db.execute(delete(Book).where(Book.id.in_(book_ids)))

    if commit:
        await db.commit()

    for chapter_id in chapter_ids:
        search_service.delete_chapter_from_index(chapter_id)
    for book_id in book_ids:
        search_service.delete_book_from_index(book_id)

    return len(book_ids)
