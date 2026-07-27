"""AliceSW site configuration — URLs, selectors, and rate limits."""

from dataclasses import dataclass, field


@dataclass
class AliceSWConfig:
    base_url: str = "https://www.alicesw.com"

    # URL templates
    bookshelf_url: str = "/bookshelf"
    book_url: str = "/book/{book_id}"
    chapter_url: str = "/book/{book_id}/chapter/{chapter_id}"
    chapter_list_url: str = "/book/{book_id}/chapters"

    # CSS selectors — adjust these to match the actual site structure
    selectors: dict = field(default_factory=lambda: {
        # Bookshelf page
        "bookshelf_item": "div.bookshelf-item, li.book-entry",
        "bookshelf_title": "a.book-title, h3.book-name",
        "bookshelf_author": "span.book-author, .author-name",
        "bookshelf_link": "a.book-title, a.book-cover",
        "bookshelf_latest": "span.latest-chapter, .update-info",

        # Book detail page
        "book_title": "h1.book-title, .novel-title",
        "book_author": "span.author, .novel-author a",
        "book_cover": "img.book-cover, .novel-cover img",
        "book_description": "div.description, .novel-desc, .intro",
        "book_status": "span.status, .book-status",

        # Chapter list
        "chapter_item": "li.chapter-item, dd.chapter-item, tr.chapter-row",
        "chapter_link": "a",
        "chapter_title": "a, span.chapter-name",

        # Chapter content
        "chapter_content": "div.content, div#content, .chapter-content",
        "chapter_title_sel": "h1.chapter-title, .chapter-name",
    })

    # Rate limiting
    request_interval: float = 2.0  # seconds between requests
    max_retries: int = 3

    # Encoding
    page_encoding: str = "utf-8"


default_config = AliceSWConfig()
