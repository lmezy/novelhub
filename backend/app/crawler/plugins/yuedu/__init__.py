"""YueDu book source plugin -- interprets YueDu (Legado) source JSON to crawl novels.

Each instance is configured with a single YueDu book source JSON. The plugin
translates YueDu's rule-based definitions (JSONPath, CSS selectors, URL templates)
into NovelHub's NovelSourcePlugin protocol.

Usage:
    plugin = YueduPlugin(yuedu_source_json)
    book = await plugin.fetch_book(url)
    content = await plugin.fetch_chapter_content(chapter)
"""

import asyncio
import base64
import codecs
import json
import logging
import os
import random
import re
import time
from html import unescape as html_unescape
from typing import Any
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
    urlunsplit,
    urlunparse,
)

import httpx
from bs4 import BeautifulSoup, Tag

from app.crawler.base import EmptyTocError, RemoteBook, RemoteChapter, RemoteShelfBook
from app.crawler.plugins.yuedu.js_runtime import JsRuntime, try_eval_js_pattern
from app.crawler.plugins.yuedu.rule_engine import YueduRuleEngine
from app.crawler.plugins.yuedu.urls import UrlsMixin
from app.crawler.plugins.yuedu.parsing import ParsingMixin
from app.crawler.plugins.yuedu.explore import ExploreMixin
from app.crawler.plugins.yuedu.book import BookMixin
from app.crawler.plugins.yuedu.chapter import ChapterMixin
from app.crawler.plugins.yuedu.images import ImagesMixin
from app.crawler.plugins.yuedu.bookshelf import BookshelfMixin
from app.crawler.plugins.yuedu.auth import AuthMixin
from app.crawler.plugins.yuedu.render import RenderMixin
from app.crawler.plugins.yuedu.page_kind import PageKindMixin
from app.crawler.plugins.yuedu.transport import TransportMixin

logger = logging.getLogger(__name__)


# The page-classification vocabulary (``markers``), the generic selector
# vocabularies (``selectors``) and the retryable-failure taxonomy (``errors``)
# used to be defined in this module.  They are re-exported here so that existing
# callers -- and the tests that do ``from app.crawler.plugins.yuedu import ...``
# -- keep working unchanged.
from app.crawler.plugins.yuedu.errors import (  # noqa: F401
    TRANSIENT_TRANSPORT_ERROR_NAMES,
    is_transient_transport_error,
)
from app.crawler.plugins.yuedu.markers import (  # noqa: F401
    CF_CHALLENGE_MARKERS,
    CLOUDFLARE_ERROR_IDS,
    CLOUDFLARE_ERROR_PHRASES,
    CONTEXTUAL_BLOCK_HINTS,
    CONTEXTUAL_BLOCK_MARKERS,
    PROSE_GATE_MARKERS,
    REMOVED_PAGE_MARKERS,
    STRONG_BLOCK_MARKERS,
    UPSTREAM_ERROR_BODY_RE,
    UPSTREAM_ERROR_CODE_RE,
    UPSTREAM_ERROR_PAGE_MAX_CHARS,
    WEAK_BLOCK_CONFIRMATIONS,
    WEAK_BLOCK_MARKERS,
    WEAK_BLOCK_WINDOW,
    has_contextual_block_marker,
    has_weak_block_marker,
)
from app.crawler.plugins.yuedu.selectors import (  # noqa: F401
    BOOKSHELF_LINK_PATTERNS,
    BOOKSHELF_PATH_CANDIDATES,
    CHAPTER_PATH_SEGMENTS,
    GENERIC_BOOK_AUTHOR_SELECTORS,
    GENERIC_BOOK_COVER_SELECTORS,
    GENERIC_BOOK_DESC_SELECTORS,
    GENERIC_BOOK_TITLE_SELECTORS,
    GENERIC_CHAPTER_SELECTORS,
    NAV_PATH_SEGMENTS,
    SHELF_AUTHOR_SELECTORS,
    SHELF_ITEM_SELECTORS,
    SHELF_LINK_SELECTORS,
    TOC_LINK_PATH_RE,
    TOC_LINK_TEXTS,
    TOC_NOISE_TITLES,
    _HOST_ONLY_URL_PATTERN,
)

class YueduPlugin(UrlsMixin, ParsingMixin, ExploreMixin, BookMixin, ChapterMixin, ImagesMixin, BookshelfMixin, AuthMixin, RenderMixin, PageKindMixin, TransportMixin):
    """A NovelSourcePlugin implementation driven by a YueDu book source JSON."""

    name = "yuedu"
    _clients: dict[str | None, httpx.AsyncClient] = {}
    # Clients replaced by ``_reset_http_client``.  They are closed by a
    # background task after the in-flight requests holding them finished.
    _retired_clients: list[tuple[asyncio.Task, asyncio.AbstractEventLoop]] = []
    # Transport health, keyed by "<source base url>::proxy|direct".  A proxy
    # (or a direct connection) that just hung or refused is remembered so the
    # next request does not pay for the dead path first.
    _transport_bad_until: dict[str, float] = {}
    _transport_preferred: dict[str, str] = {}
    # ``{image url: expiry}`` for images a CDN answered 404/410 on.  Manga
    # pages reference placeholders (321cdn's ``/img/88.webp``) that never
    # exist; the response is permanent, so remembering it stops every chapter
    # from paying three retries for the same dead URL.
    _missing_image_urls: dict[str, float] = {}
    _missing_image_ttl = 1800.0
    _missing_image_limit = 2048
    _rate_locks: dict[str, asyncio.Lock] = {}
    _rate_state: dict[str, dict[str, float | int]] = {}
    # DoH (DNS over HTTPS) cache for bypassing polluted system DNS.
    # {host: {"ip": ip, "expires": epoch_seconds}}
    _doh_cache: dict[str, dict[str, float | str]] = {}
    _doh_lock = asyncio.Lock()
    # ``{base_url + rule: headers}`` memo for the source's ``header`` rule: the
    # rule is a JS script in many exported sources, and evaluating it on every
    # request would be pure overhead.
    _header_rule_cache: dict[str, dict[str, str]] = {}
    _doh_providers = (
        # Tencent public DNS works from CN networks; try it first.
        "https://doh.pub/dns-query",
        "https://cloudflare-dns.com/dns-query",
        "https://dns.google/resolve",
    )
    _doh_ttl = 300

    def __init__(self, source_config: dict[str, Any] | None = None):
        self.config = self._normalize_source_config(source_config)
        self.engine: YueduRuleEngine | None = None
        if self.config:
            self.engine = YueduRuleEngine(self.config)
        self.base_url: str = self.config.get("bookSourceUrl", "")
        self._cookie: str = ""
        # The cookie the user configured (a stored shelf cookie, via
        # ``set_cookie``), as opposed to session cookies the *site* hands out in
        # ``Set-Cookie`` and which ``_capture_cookie_jar`` folds into
        # ``_cookie``.  Only the former makes the "your Cookie expired" hint
        # true: a bare ``fontsize`` preference cookie was enough to tell the
        # user to re-import a Cookie the source never had.
        self._configured_cookie: str = ""
        self._client_lock = asyncio.Lock()
        # Pagination templates learned from a catalog page.  Sources such as
        # 風月文學網 h528 list categories without a ``{{page}}`` placeholder,
        # so Legado/NovelHub have to page through ``.../page/2`` themselves.
        # ``{base_url: template}`` where the template contains ``{page}``.
        self._explore_page_templates: dict[str, str] = {}
        # ``{fetched_url: one-line page summary}`` for catalog pages that
        # parsed to zero books, so the "returned no books" warning can say
        # what the site actually answered instead of only the URL.
        self._explore_page_diagnostics: dict[str, str] = {}
        # ``{kind_url: page-1 URL}`` memo so a ``<js>`` rule is not evaluated
        # twice per page.
        self._explore_kind_bases: dict[str, str] = {}
        # Manga sources often collect ``imgInfoList`` while parsing the TOC.
        # Keep the manifest on the plugin instance so later chapter fetches do
        # not depend on the shared Node runtime's mutable global cache (several
        # books may sync concurrently).
        self._chapter_image_manifest: list[dict[str, str]] = []
        # Per-source request interval (seconds) configured in the admin UI.
        # ``None`` means "not configured": the source's own ``concurrentRate``
        # decides, then ``CRAWL_DELAY_MS``.  0 means "explicitly unthrottled".
        self._request_interval_seconds: int | None = None

    def set_request_interval_seconds(self, seconds: int | None) -> None:
        """Override how often this source may issue one upstream request.

        Site operators publish a 拉取间隔 ("no more than one request per N
        seconds") that the book source JSON often understates -- 搬山人 ships
        ``concurrentRate: 1000`` while its real limit is a minute -- so the
        admin UI can set an explicit interval per source.  ``None`` keeps the
        source's ``concurrentRate``.
        """
        if seconds is None:
            self._request_interval_seconds = None
            return
        try:
            value = int(seconds)
        except (TypeError, ValueError):
            self._request_interval_seconds = None
            return
        self._request_interval_seconds = max(0, value)

    @property
    def display_name(self) -> str:
        return self.config.get("bookSourceName", "yuedu")

    @property
    def source_group(self) -> str:
        return self.config.get("bookSourceGroup", "")

    def configure(self, config: dict[str, Any] | None) -> None:
        """Load a YueDu book source JSON configuration."""
        if not config:
            raise ValueError("YueduPlugin requires a valid book source JSON config")
        self.config = self._normalize_source_config(config)
        self.engine = YueduRuleEngine(self.config)
        self.base_url = self.config.get("bookSourceUrl", "")
        self._chapter_image_manifest = []

    @staticmethod
    def _normalize_source_config(
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Accept exported YueDu sources that use [] for an empty rule map."""
        normalized = dict(config or {})
        for field in ("ruleSearch", "ruleExplore", "ruleBookInfo", "ruleToc", "ruleContent"):
            if not isinstance(normalized.get(field), dict):
                normalized[field] = {}
        return normalized













    # Chromium is launched per webJs/webView fetch.  Sync runs several books
    # and chapters at once, so without a cap a NAS-sized host ends up with
    # dozens of browsers, which shows up as "empty browser page" failures and
    # a steadily growing pile of chrome/crashpad processes.
    _browser_semaphores: dict[int, asyncio.Semaphore] = {}







    # ---- Required: fetch_book ----













    _NAV_CONTAINER_KEYWORDS = (
        "nav",
        "menu",
        "header",
        "footer",
        "breadcrumb",
        "crumb",
        "toolbar",
        "topbar",
        "sidebar",
    )











    # Listing titles that describe a ranking/sort view rather than a genre.
    _LISTING_ONLY_KIND_RE = re.compile(
        r"(?:排行|榜单|榜|最新|最近更新|全部|首页|书库|完本|完结|推荐|入库)",
    )





    # ---- Required: fetch_chapter_content ----





    # Images that are clearly site chrome rather than chapter content.
    _NOISE_IMAGE_RE = re.compile(
        r"logo|avatar|icon|sprite|banner|advert|qrcode|blank|placeholder"
        r"|loading|spacer|pixel|button",
        re.IGNORECASE,
    )
    _IMAGE_FILE_RE = re.compile(
        r"\.(?:jpe?g|png|webp|gif|bmp|avif)(?:[?#]|$)", re.IGNORECASE
    )






    # ---- Required: fetch_bookshelf ----


    # ---- Generic bookshelf HTML parser ----









    # ---- Bookshelf URL auto-detection ----


    # ---- Login check ----




    # ---- Explore / Discover ----






    # ---- Catalog pagination for URLs without a ``{{page}}`` placeholder ----
    #
    # Legado sources frequently configure a plain category URL and expect the
    # client to walk the site's own paging links (風月文學網 h528:
    # ``/post/category/<cat>`` -> ``/post/category/<cat>/page/2``).  Without
    # this the resolver returned the *same* URL for every page, so discovery
    # saw page 2 as a page of duplicates, stopped and reported the source as
    # fully synchronized after a single page ("only 360 books, then complete").
    _PAGE_PATH_RES = (
        re.compile(r"^(?P<prefix>.*?)(?P<sep>/page/)(?P<num>\d+)(?P<suffix>/?)$"),
        re.compile(
            r"^(?P<prefix>.*?)(?P<sep>/(?:index|list)_)(?P<num>\d+)"
            r"(?P<suffix>\.html?)$"
        ),
    )

















    # ---- Optional: update_book ----








    async def update_book(self, url: str) -> RemoteBook | None:
        """Re-fetch a book to check for new chapters."""
        try:
            return await self.fetch_book(url)
        except Exception as e:
            logger.error(f"YueduPlugin.update_book failed: {e}")
            return None

    # ---- Cookie support ----



    # ---- Helpers ----








































