"""Selector and path vocabularies for the YueDu (Legado) plugin.

Split out of ``plugins/yuedu/__init__.py``: these are the generic
CSS/link vocabularies the fallback parsers use when a book source
does not declare a rule for something.  Leaf module -- it imports
nothing from this package, so it can never create an import cycle.
"""

import re


# Common bookshelf URL patterns across Chinese novel sites
BOOKSHELF_PATH_CANDIDATES = [
    "/user/bookshelf",
    "/bookshelf",
    "/mybook",
    "/bookcase",
    "/user/favorites",
    "/favorites",
    "/user/collect",
    "/collect",
    "/mybooks",
    "/user/books",
    "/member/bookshelf",
    "/user",
    "/user/bookcase",
    "/user/shelf",
    "/home/bookcase",
    "/home/bookshelf",
    "/space/bookshelf",
    "/reader/bookshelf",
    "/book/shelf",
    "/books",
    "/my",
    "/ucenter/bookshelf",
    "/ucenter",
    "/center",
    "/personal",
]

# Common link text patterns that indicate a bookshelf link
BOOKSHELF_LINK_PATTERNS = [
    "书架", "我的书架", "bookshelf", "收藏", "我的收藏",
    "favorites", "my bookshelf", "bookcase", "书柜",
    "我的书柜", "追书", "我的追书",
]

# CSS selectors that commonly identify book entries on bookshelf pages
SHELF_ITEM_SELECTORS = [
    "li.book-item", "li.book-entry", "li.shelf-item",
    "div.book-item", "div.book-entry", "div.shelf-item",
    "tr.book-row", "tr.shelf-row",
    "li.bookshelf-item", "div.bookshelf-item",
    "li[class*='book']", "div[class*='book']",
    "li[class*='shelf']", "div[class*='shelf']",
    # Very generic fallback: any li or div with an anchor inside
    "li:has(a[href])", "div.book-card",
    # Additional patterns for Chinese novel sites
    "div.panel-body li", "ul.list-group li",
    "div.card li", "div.panel li",
    "table.table tr", "ul.novel-list li",
    "div[class*='shelf'] a[href]",
]

# Anchor patterns within a shelf item
SHELF_LINK_SELECTORS = [
    "a.book-title", "a[class*='title']", "a[class*='name']",
    "h3 a", "h2 a", "h4 a",
    "a:first-child", "a",
]

# Author patterns within a shelf item
SHELF_AUTHOR_SELECTORS = [
    "span.author", "span[class*='author']", "span[class*='writer']",
    ".book-author", ".author-name", "span:nth-child(2)",
    "p.author", "span[class*='by']",
]

GENERIC_BOOK_TITLE_SELECTORS = [
    "h1",
    ".book-name",
    ".book_name",
    ".novel-title",
    ".bookTitle",
    ".info h1",
    ".bookinfo h1",
    ".book_info h1",
    "meta[property='og:title']",
    "meta[name='og:title']",
]

GENERIC_BOOK_AUTHOR_SELECTORS = [
    "meta[property='og:novel:author']",
    "meta[name='author']",
    ".book-author",
    ".author",
    ".writer",
    ".info .author",
    ".bookinfo .author",
    ".book_info .author",
]

GENERIC_BOOK_DESC_SELECTORS = [
    "meta[name='description']",
    ".book-intro",
    ".book_intro",
    ".book-desc",
    ".intro",
    ".desc",
    ".book-description",
    "#intro",
]

GENERIC_CHAPTER_SELECTORS = [
    "div.book_newchap a",
    ".book_newchap a",
    "#chapters a",
    ".chapter-list a",
    ".listmain a",
    "ul.chapter-list a",
    "div.listmain a",
    "li.chapter-item a",
    "dd.chapter a",
    ".book-catalog a",
    "#catalog a",
    "#list a",
    "#content a",
    "[class*='chapter'] a",
    "[class*='catalog'] a",
    "[class*='list'] a",
]

GENERIC_BOOK_COVER_SELECTORS = [
    "meta[property='og:image']",
    "meta[name='og:image']",
    "meta[itemprop='image']",
    "img.book-cover",
    ".book-cover img",
    ".novel-cover img",
    ".book_info img",
    ".book-info img",
    ".bookinfo img",
    "#cover img",
    "img.cover",
    "img[class*='cover']",
]

TOC_LINK_TEXTS = {
    "查看所有章节",
    "查看全部章节",
    "全部章节",
    "章节列表",
    "章节目录",
    "查看目录",
    "所有章节",
    "目录",
}

TOC_LINK_PATH_RE = re.compile(
    r"/(?:other/chapters|chapters|booktoc|chapterlist|toc|book/chapters)(?:/|\.)",
    re.IGNORECASE,
)

CHAPTER_PATH_SEGMENTS = (
    "book",
    "read",
    "chapter",
    "chapters",
    "novel",
    "article",
    "content",
    "view",
    "show",
    "xiaoshuo",
    "txt",
    "files",
)

NAV_PATH_SEGMENTS = (
    "list",
    "lists",
    "sort",
    "rank",
    "top",
    "all",
    "order",
    "update",
    "finish",
    "wanben",
    "quanben",
    "allvisit",
    "lastupdate",
    "history",
    "bookcase",
    "bookshelf",
    "user",
    "users",
    "login",
    "register",
    "signup",
    "search",
    "category",
    "tag",
    "tags",
    "author",
    "about",
    "help",
    "faq",
    "contact",
    "index",
    "original",
    "other",
    "fenlei",
    "booklist",
)

# ``bookUrlPattern`` values that only name the site ("https://host:port/"):
# they match every URL on that host, so they cannot filter book links (Icu).
_HOST_ONLY_URL_PATTERN = re.compile(r"^https?://[^/?#]+/?$", re.I)

TOC_NOISE_TITLES = {
    "首页",
    "原创",
    "最新",
    "电子魅魔",
    "Ai性伴侣",
    "色情游戏",
    "查看所有章节",
    "查看全部章节",
    "全部章节",
    "章节列表",
    "章节目录",
    "目录",
    "返回书页",
    "直达底部",
    "简体站",
    "繁體站",
    "发布页",
    "上一章",
    "下一章",
    "返回目录",
    "开始阅读",
}
