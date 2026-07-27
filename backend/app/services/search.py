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


search_service = SearchService()
