from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Author, Book, BookTag, Chapter, Tag
from app.repositories.tag import TagRepository
from app.services.search import search_service
from app.services.storage import BookStorage
from app.services.r18 import detect_r18


class ManualImportService:
    """Create a book directly from user-provided metadata and chapter text."""

    def __init__(self, db: AsyncSession, storage: BookStorage | None = None):
        self.db = db
        self.storage = storage or BookStorage()

    async def import_book(
        self,
        title: str,
        author: str,
        description: str | None,
        status: str,
        tags: list[str],
        chapters: list[dict],
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("Book title is required")
        if not chapters:
            raise ValueError("At least one chapter is required")

        author_name = (author or "").strip() or "未知作者"
        db_author = await self.db.scalar(
            select(Author).where(Author.name == author_name)
        )
        if db_author is None:
            db_author = Author(id=str(uuid4()), name=author_name)
            self.db.add(db_author)
            await self.db.flush()

        is_r18 = detect_r18(
            title=title,
            author=author_name,
            description=description,
            tags=tags,
        )
        book = Book(
            id=str(uuid4()),
            author_id=db_author.id,
            title=title,
            description=description or None,
            status=status or "ongoing",
            is_r18=is_r18,
        )
        self.db.add(book)
        await self.db.flush()

        classification_tag = "r18" if is_r18 else "all-ages"
        await self._save_tags(book.id, [*tags, classification_tag])

        self.storage.write_metadata(
            author_name,
            title,
            {
                "source_id": None,
                "source_book_id": None,
                "title": title,
                "author": author_name,
                "description": description,
                "status": status,
                "is_r18": is_r18,
                "tags": tags,
            },
        )
        search_service.index_book({
            "id": book.id,
            "title": book.title,
            "description": book.description or "",
            "status": book.status or "",
            "source_id": "",
            "author_id": book.author_id or "",
            "is_r18": book.is_r18,
        })

        created = 0
        for index, chapter in enumerate(chapters, start=1):
            chapter_title = (chapter.get("title") or "").strip() or f"第 {index} 章"
            content = chapter.get("content") or ""
            content_path, content_hash = self.storage.write_chapter(
                author_name,
                title,
                index,
                chapter_title,
                content,
            )
            chapter_row = Chapter(
                id=str(uuid4()),
                book_id=book.id,
                chapter_number=index,
                source_chapter_id=f"manual-{index}",
                title=chapter_title,
                content_path=content_path,
                hash=content_hash,
            )
            self.db.add(chapter_row)
            await self.db.flush()
            search_service.index_chapter({
                "id": chapter_row.id,
                "book_id": book.id,
                "title": chapter_title,
                "chapter_number": index,
                "content": content[:5000],
                "is_r18": book.is_r18,
            })
            created += 1

        await self.db.commit()

        try:
            from app.services.auto_categorize import AutoCategorizationService
            await AutoCategorizationService.categorize_book(self.db, book.id)
        except Exception:
            await self.db.rollback()

        return {"book_id": book.id, "created_chapters": created}

    async def _save_tags(self, book_id: str, tag_names: list[str]) -> None:
        repo = TagRepository(self.db)
        seen: set[str] = set()
        for name in tag_names:
            name = (name or "").strip().lower()
            if not name or name in seen:
                continue
            seen.add(name)
            tag = await repo.get_by_name(name)
            if tag is None:
                tag = Tag(id=str(uuid4()), name=name)
                self.db.add(tag)
                await self.db.flush()
            self.db.add(BookTag(book_id=book_id, tag_id=tag.id))
        await self.db.flush()
