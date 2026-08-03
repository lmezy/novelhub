import meilisearch
from loguru import logger

from app.core.config import settings


class SearchService:
    INDEX_BOOKS = "books"
    INDEX_CHAPTERS = "chapters"

    def __init__(self):
        self.client = meilisearch.Client(settings.MEILI_HOST, settings.MEILI_KEY)

    def _ensure_index(self, name: str, primary_key: str = "id") -> None:
        try:
            self.client.get_index(name)
        except meilisearch.errors.MeilisearchApiError:
            self.client.create_index(name, {"primaryKey": primary_key})
            self.client.index(name).update_filterable_attributes(["source_id", "book_id", "author_id"])

    def index_book(self, book: dict) -> None:
        self._ensure_index(self.INDEX_BOOKS)
        self.client.index(self.INDEX_BOOKS).add_documents([book])
        logger.debug("Indexed book {}", book.get("id"))

    def index_chapter(self, chapter: dict) -> None:
        self._ensure_index(self.INDEX_CHAPTERS)
        self.client.index(self.INDEX_CHAPTERS).add_documents([chapter])
        logger.debug("Indexed chapter {}", chapter.get("id"))

    def search_books(self, query: str, *, offset: int = 0, limit: int = 20) -> dict:
        return self.client.index(self.INDEX_BOOKS).search(query, {"offset": offset, "limit": limit})

    def search_chapters(self, query: str, *, offset: int = 0, limit: int = 20) -> dict:
        return self.client.index(self.INDEX_CHAPTERS).search(query, {"offset": offset, "limit": limit})

    def delete_book(self, book_id: str) -> None:
        try:
            self.client.index(self.INDEX_BOOKS).delete_document(book_id)
            logger.debug("Deleted book {} from search index", book_id)
        except meilisearch.errors.MeilisearchApiError:
            pass

    def delete_chapter(self, chapter_id: str) -> None:
        try:
            self.client.index(self.INDEX_CHAPTERS).delete_document(chapter_id)
            logger.debug("Deleted chapter {} from search index", chapter_id)
        except meilisearch.errors.MeilisearchApiError:
            pass

    def delete_chapter_from_index(self, chapter_id: str) -> None:
        try:
            self.client.index(self.INDEX_CHAPTERS).delete_document(chapter_id)
            logger.debug("Deleted chapter {} from search index", chapter_id)
        except Exception:
            pass

    def get_index_stats(self) -> dict:
        """Return document counts for both indexes."""
        result = {}
        for idx_name in [self.INDEX_BOOKS, self.INDEX_CHAPTERS]:
            try:
                idx = self.client.get_index(idx_name)
                stats = idx.get_stats()
                result[idx_name] = {
                    "documents": stats.number_of_documents,
                    "is_indexing": stats.is_indexing,
                    "last_update": getattr(stats, "updated_at", None),
                }
            except Exception:
                result[idx_name] = {"documents": 0, "is_indexing": False, "last_update": None}
        return result

    def rebuild_index(self) -> dict:
        """Rebuild both indexes from database records."""
        import asyncio
        result = {"books": 0, "chapters": 0}

        async def _rebuild():
            from app.core.database import SessionLocal
            from app.models import Book, Chapter
            from sqlalchemy import select

            async with SessionLocal() as db:
                # Delete and recreate indexes
                try:
                    self.client.delete_index(self.INDEX_BOOKS)
                except Exception:
                    pass
                try:
                    self.client.delete_index(self.INDEX_CHAPTERS)
                except Exception:
                    pass
                self._ensure_index(self.INDEX_BOOKS)
                self._ensure_index(self.INDEX_CHAPTERS)

                # Reindex books
                books = await db.scalars(select(Book))
                book_list = list(books)
                for b in book_list:
                    self.index_book({
                        "id": b.id,
                        "title": b.title,
                        "description": b.description or "",
                        "status": b.status or "",
                        "source_id": b.source_id or "",
                        "author_id": b.author_id or "",
                    })
                result["books"] = len(book_list)

                # Reindex chapters (batched to avoid memory issues)
                chapters = await db.scalars(
                    select(Chapter).limit(5000)
                )
                batch = []
                for ch in chapters:
                    batch.append({
                        "id": ch.id,
                        "book_id": ch.book_id,
                        "title": ch.title or "",
                        "chapter_number": ch.chapter_number or 0,
                    })
                if batch:
                    self.client.index(self.INDEX_CHAPTERS).add_documents(batch)
                result["chapters"] = len(batch)

        asyncio.run(_rebuild())
        return result

    def delete_book_from_index(self, book_id: str) -> None:
        try:
            self.client.index(self.INDEX_BOOKS).delete_document(book_id)
            logger.debug("Deleted book {} from search index", book_id)
        except Exception:
            pass

search_service = SearchService()
