import asyncio
import re
from collections import Counter
from types import SimpleNamespace
from uuid import uuid4
from collections.abc import Awaitable, Callable
from html import escape, unescape
from urllib.parse import urljoin, urlparse

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from loguru import logger

from app.crawler.base import EmptyTocError
from app.crawler.plugins.yuedu.markers import chapter_text_is_blocked
from app.crawler.registry import get_plugin
from app.core import transient as core_transient
from app.core.config import settings, sync_book_concurrency, sync_thread_count
from app.core.database import SessionLocal
from app.models import (
    Author,
    Book,
    BookCategory,
    BookCustomTag,
    BookFavorite,
    BookTag,
    BookVersion,
    Category,
    Chapter,
    ChapterEmbedding,
    Cookie,
    Bookmark,
    CustomTag,
    ReadingProgress,
    Source,
    SourceChange,
    Tag,
    User,
)
from app.repositories.tag import TagRepository
from app.services.content_quality import SynopsisCatalog, cover_wall_labels, foreign_synopsis, match_cover_titles, normalize_title
from app.services.storage import BookRef, BookStorage, ChapterFileBatch, TruncatedChapterError, text_digest
from app.services.search import search_service
from app.services.cookie_crypto import safe_decrypt_cookie
from app.services.r18 import detect_r18
from app.services.auto_categorize import classify_category_names
from app.services.book_title import is_identity_tag, normalized_title_sql
from app.services.source_interval import apply_source_interval, source_sync_interval
from app.services.book_kind import (
    KIND_COMIC,
    classify_stored_book,
    is_comic_content,
    is_comic_source,
    normalize_kind,
    refresh_search_kinds,
    resolve_kind,
)


class SyncPaused(Exception):
    """Raised by a task checkpoint when a crawl task has been paused."""


def _normalize_filter_value(value: str | None) -> str:
    return re.sub(r"[\s\-_./|]+", "", str(value or "")).lower()


def _matches_exclusion(values: list[str], exclusions: list[str]) -> str | None:
    normalized_values = [_normalize_filter_value(value) for value in values]
    for exclusion in exclusions:
        needle = _normalize_filter_value(exclusion)
        if needle and any(needle in value for value in normalized_values):
            return exclusion
    return None


JUNK_CHAPTER_TITLES = {
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

JUNK_CHAPTER_PATH_SEGMENTS = (
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

HTML_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
MD_IMG_RE = re.compile(
    r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)"
)
CONTENT_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*\bsrc=",
    re.IGNORECASE,
)

# The transient *exception* taxonomy lives in exactly one place:
# ``app.core.transient``.  This module used to keep its own list, which drifted
# from the plugin's -- ``EndOfStream``/``WouldBlock`` were retryable in the
# request loop but permanent here, so such a failure could count toward the
# "连续失败" abort.  Classification goes through ``core_transient`` directly
# (see ``_is_transient_book_fetch``); nothing re-exports the name list from
# here, and ``tests/test_transient_taxonomy.py`` fails if that changes.

def describe_error(exc: BaseException | None) -> str:
    """Human-readable error text that never comes back empty.

    ``httpx.ReadTimeout()`` and ``asyncio.TimeoutError()`` stringify to "",
    which made crawl logs and task errors read like
    ``Failed to sync book X (url): `` with no cause at all.
    """
    if exc is None:
        return "Unknown error"
    text = str(exc).strip()
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        cause_text = str(cause).strip() or type(cause).__name__
        if not text:
            text = cause_text
        elif cause_text not in text:
            text = f"{text} ({cause_text})"
    return text or type(exc).__name__


def content_image_limit(*, comic: bool = False) -> int:
    """A positive override applies to all chapters. By default only prose is capped."""
    limit = int(getattr(settings, "MAX_CONTENT_IMAGES_PER_CHAPTER", 0) or 0)
    if limit > 0:
        return limit
    return 0 if comic else 512


def _last_explore_diagnosis(plugin) -> str:
    """Append what the site actually answered to an "empty catalog" error.

    The yuedu plugin records a one-line summary (bytes, title, first words) for
    every catalog page that parsed to zero books.  Without it a Cloudflare
    "520: Web server is returning an unknown error" page looks exactly like a
    source whose discovery rules stopped matching the site.
    """
    diagnostics = getattr(plugin, "_explore_page_diagnostics", None)
    if not isinstance(diagnostics, dict) or not diagnostics:
        return ""
    url, summary = list(diagnostics.items())[-1]
    summary = " ".join(str(summary or "").split())
    if not summary:
        return ""
    if len(summary) > 200:
        summary = summary[:200] + "…"
    return f" 最近一次分类页：{url} → {summary}"


def _last_book_diagnosis(plugin) -> str:
    """Append what the site answered to a "no usable chapters" book error.

    Mirrors ``_last_explore_diagnosis``: the yuedu plugin records one line for
    every book page whose table of contents parsed to zero chapters, and that
    line is the difference between "换代理节点" and "书源规则过期了" -- the
    live 要撸小说 failure looked identical for both (codex-handoff 62).
    """
    diagnosis = getattr(plugin, "_book_page_diagnosis", None)
    if not diagnosis:
        return ""
    try:
        url, summary = diagnosis
    except (TypeError, ValueError):
        return ""
    summary = " ".join(str(summary or "").split())
    if not summary:
        return ""
    if len(summary) > 200:
        summary = summary[:200] + "…"
    return f" 书页诊断：{url} → {summary}"


async def load_source_cookie(source_id: str) -> str | None:
    """Read a source's stored Cookie on its own short-lived session.

    Every call site below used to read it on ``self.db`` -- the session that then
    crawls the whole book.  Postgres holds the ``ACCESS SHARE`` lock taken by
    that read until the transaction ends, so ``cookies`` stayed locked for
    minutes (pg_stat_activity showed the transaction parked at ``idle in
    transaction`` with the lock granted).  A deployment's ``ALTER TABLE
    cookies`` then queued for ``ACCESS EXCLUSIVE`` behind it, and because the
    container starts with ``alembic upgrade head && uvicorn`` the server never
    came up: nginx answered 502 for every request, login included, until the
    crawler's transaction happened to end.

    Cookies are never written by a sync, so reading them in a transaction of
    their own changes nothing but the lock's lifetime.  Returns the still
    encrypted value; callers decrypt with ``safe_decrypt_cookie``.
    """
    async with SessionLocal() as db:
        return await db.scalar(
            select(Cookie.cookie_data).where(Cookie.source == source_id)
        )


from app.services.sync_book_meta import SyncBookMetaMixin
from app.services.sync_errors import SyncErrorMixin, is_transient_task_error


class SyncService(SyncBookMetaMixin, SyncErrorMixin):
    def __init__(self, db: AsyncSession, storage: BookStorage | None = None, *, synopses: SynopsisCatalog | None = None):
        self.db = db
        self.storage = storage or BookStorage()
        self._synopses = synopses or SynopsisCatalog()
        #: Every book title in the library, loaded once per sync run; the
        #: cover-wall detector compares image alt texts against it.
        self._other_book_titles: set[str] | None = None

    def _source_plugin(self, source: Source):
        """Build the plugin for a source, carrying its request interval.

        The interval is a property of the site, so it is read from the source
        row on every task start -- a sync that is already running keeps the
        pacing it began with, and the next one picks up an edit made in the
        admin UI in between.
        """
        config = source.config if source.plugin_name == "yuedu" else None
        plugin = get_plugin(source.plugin_name, config=config)
        seconds = apply_source_interval(plugin, source)
        if seconds:
            logger.debug(
                "Source {} throttled to one request per {}s",
                source.id,
                seconds,
            )
        return plugin

    @staticmethod
    def _safe_text(value, fallback: str) -> str:
        text = str(value).strip() if value else ""
        return text or fallback

    @staticmethod
    def _safe_title(remote_book) -> str:
        return SyncService._clamp(
            SyncService._safe_text(
                remote_book.title,
                remote_book.source_book_id or "Unknown",
            ),
            255,
        )

    @staticmethod
    def _safe_author(name: str | None) -> str:
        return SyncService._clamp(SyncService._safe_text(name, "Unknown"), 100)

    @staticmethod
    def _clamp(value: str, maxlen: int) -> str:
        """Clamp to a DB column limit so a malformed over-long parsed value
        cannot raise StringDataRightTruncationError."""
        text = str(value or "")
        return text if len(text) <= maxlen else text[:maxlen]

    @staticmethod
    async def _fetch_chapter_with_retry(
        plugin,
        remote_chapter,
        attempts: int = 3,
        progress_cb: Callable[[int], Awaitable[None]] | None = None,
    ) -> str:
        """Fetch a chapter, retrying transient network errors."""
        last_error: Exception | None = None
        supports_page_progress = callable(
            getattr(type(plugin), "fetch_chapter_content_with_progress", None)
        )
        for attempt in range(attempts):
            try:
                if progress_cb is not None and supports_page_progress:
                    return await plugin.fetch_chapter_content_with_progress(
                        remote_chapter,
                        progress_cb,
                    )
                return await plugin.fetch_chapter_content(remote_chapter)
            except Exception as exc:
                last_error = exc
                if (
                    isinstance(exc, core_transient.UpstreamStatusError)
                    or not SyncService._is_transient_chapter_error(exc)
                    or SyncService._is_upstream_blocked(exc)
                    or SyncService._is_permanent_chapter_error(exc)
                ):
                    raise
                if attempt < attempts - 1:
                    await asyncio.sleep((2 ** attempt) + 0.5)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Chapter fetch failed")

    @staticmethod
    def _chapter_concurrency(
        config: dict | None,
        source_interval: int | None = None,
    ) -> int:
        """Pick chapter fetch concurrency, mirroring Legado's thread model."""
        default = min(
            max(1, int(getattr(settings, "SYNC_CHAPTER_CONCURRENCY", 9))),
            sync_thread_count(),
        )
        if getattr(settings, "SYNC_IGNORE_RATE_LIMIT", False):
            return default
        if source_interval:
            # The configured interval already lets one request through at a
            # time; extra chapter tasks would only park on that limiter while
            # holding their producer slot.
            return 1
        if not config:
            return default
        rate = str(config.get("concurrentRate", "") or "").strip()
        if "/" in rate:
            try:
                count = int(rate.split("/", 1)[0].strip())
            except ValueError:
                count = 0
            if count > 0:
                return min(count, 16)
        elif rate and rate != "0":
            # Legado treats a plain interval as one request per interval.
            return 1
        return default

    @staticmethod
    def _is_http_url(url: str) -> bool:
        return url.startswith(("http://", "https://"))

    @staticmethod
    def _normalize_title_for_match(title: str | None) -> str:
        return re.sub(
            r"[\s《》「」『』〈〉（）【】\[\]\"'“”‘’]+",
            "",
            title or "",
        ).lower()

    @staticmethod
    def _clean_sync_tags(
        tags: list[str] | set[str],
        title: str,
        author: str,
    ) -> list[str]:
        """Drop title/author fragments that old imports stored as tags."""
        cleaned = [str(tag or "").strip().strip("#＃").strip().lower() for tag in tags]
        return list(dict.fromkeys(tag for tag in cleaned if tag and not is_identity_tag(tag, title, author)))

    @staticmethod
    def _is_book_r18(source: Source, remote_book) -> bool:
        return (
            bool(getattr(remote_book, "is_r18", False))
            or detect_r18(
                source_is_r18=getattr(source, "is_r18", False),
                title=remote_book.title,
                author=remote_book.author,
                description=remote_book.description,
                tags=getattr(remote_book, "tags", []) or [],
            )
        )

    @staticmethod
    def _img_src_from_tag(tag: str) -> str | None:
        for attr in ("data-src", "data-original", "data-lazy-src", "src"):
            match = re.search(
                attr + r'\s*=\s*["\']([^"\']+)["\']',
                tag,
                re.IGNORECASE,
            )
            if match and match.group(1).strip():
                return match.group(1).strip()
        return None

    @staticmethod
    def _strip_content_images(content: str) -> str:
        """Remove image markup before sending content to the search index."""
        text = HTML_IMG_RE.sub("", content or "")
        return MD_IMG_RE.sub("", text)

    async def _process_content_images(
        self,
        book: Book,
        chapter_id: str,
        content: str,
        base_url: str,
        plugin,
        image_progress_cb: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> str:
        """Download in-content images and rewrite references to local URLs.

        Report each download so the queue watchdog sees progress during long albums.
        """
        if not content:
            return content
        fetcher = getattr(plugin, "fetch_content_image", None)
        if fetcher is None:
            return content

        # Record source positions before downloading. Replacing each reference
        # by searching the growing chapter again is quadratic for long albums.
        refs: list[tuple[int, int, str, str]] = []
        for match in HTML_IMG_RE.finditer(content):
            tag = match.group()
            src = self._img_src_from_tag(tag)
            if src:
                alt = re.search(r'alt\s*=\s*["\']([^"\']*)["\']', tag, re.I)
                refs.append((*match.span(), unescape(src), unescape(alt.group(1)) if alt else ""))
        for match in MD_IMG_RE.finditer(content):
            refs.append((*match.span(), match.group(2), match.group(1)))
        if not refs:
            return content
        comic = (
            getattr(book, "kind", None) == KIND_COMIC or is_comic_source(getattr(plugin, "config", None))
            or bool(getattr(plugin, "_chapter_image_manifest", None)) or is_comic_content(content)
        )
        limit = content_image_limit(comic=comic)
        if limit:
            refs = refs[:limit]

        images: list[tuple[int, int, str, str]] = []
        last_end = 0
        for start, end, src, alt in sorted(refs):
            if start < last_end or src.startswith("data:"):
                continue
            images.append((start, end, urljoin(base_url or "", src).split("#", 1)[0], alt))
            last_end = end
        if not images:
            return content

        # Workers share an iterator. Only currently active downloads allocate
        # coroutines, even when one chapter lists thousands of distinct images.
        urls = dict.fromkeys(item[2] for item in images)
        downloads = iter(enumerate(urls, 1))
        local_urls: dict[str, str] = {}

        async def download() -> None:
            for index, url in downloads:
                if image_progress_cb is not None:
                    await image_progress_cb(index, len(urls))
                try:
                    path = await asyncio.to_thread(self.storage.find_chapter_image, book.id, url)
                    if not isinstance(path, str):
                        result = await fetcher(url, referer=base_url or None)
                        if result is None:
                            continue
                        data, _content_type = result
                        path = await asyncio.to_thread(self.storage.save_chapter_image, book.id, url, data)
                    filename = path.rsplit("/", 1)[-1]
                    local_urls[url] = f"/api/chapters/{chapter_id}/images/{filename}"
                except core_transient.UpstreamBackoff:
                    raise
                except Exception:
                    # Keep the original remote image available to the reader.
                    continue

        concurrency = max(1, int(getattr(settings, "SYNC_IMAGE_CONCURRENCY", 3) or 3))
        workers = [asyncio.create_task(download()) for _ in range(min(concurrency, len(urls)))]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

        parts = []
        cursor = 0
        for start, end, url, alt in images:
            parts.append(content[cursor:start])
            src = escape(local_urls.get(url, url), quote=True)
            parts.append(f'<img src="{src}" alt="{escape(alt, quote=True)}" loading="lazy">')
            cursor = end
        parts.append(content[cursor:])
        return "".join(parts)

    async def sync_book(
        self,
        source_id: str,
        url: str,
        progress_cb: Callable[[dict], Awaitable[None]] | None = None,
        checkpoint_cb: Callable[[], Awaitable[None]] | None = None,
        is_r18_override: bool | None = None,
        discovery_tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        exclude_categories: list[str] | None = None,
        refetch_content: bool = False,
    ) -> dict:
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")
        if source.plugin_name != "local_markdown" and not self._is_http_url(url):
            raise ValueError(f"Unsupported book URL: {url}")

        logger.info("Starting sync for source={} url={}", source_id, url)

        config = source.config if source.plugin_name == 'yuedu' else None
        plugin = self._source_plugin(source)
        stored_cookie = await load_source_cookie(source_id)
        if stored_cookie:
            plugin.set_cookie(safe_decrypt_cookie(stored_cookie))
        # A single transient Cloudflare 5xx / browser timeout on the book page
        # should not fail the whole book.  Retry a few times with backoff for
        # transient upstream errors before giving up.
        async def report_toc_progress(title: str, pages: int) -> None:
            if checkpoint_cb is not None:
                await checkpoint_cb()
            if progress_cb is not None:
                await progress_cb({"book_title": title, "toc_pages_done": pages})

        track_toc = (progress_cb is not None or checkpoint_cb is not None) and callable(
            getattr(type(plugin), "fetch_book_with_progress", None)
        )
        remote_book = None
        last_book_exc: Exception | None = None
        for book_attempt in range(3):
            try:
                if track_toc:
                    remote_book = await plugin.fetch_book_with_progress(url, report_toc_progress)
                else:
                    remote_book = await plugin.fetch_book(url)
                break
            except Exception as exc:
                last_book_exc = exc
                if isinstance(exc, core_transient.UpstreamStatusError) or not self._is_transient_book_fetch(exc):
                    raise
                if book_attempt < 2:
                    await asyncio.sleep(2.0 + book_attempt * 2.0)
        if remote_book is None:
            raise last_book_exc  # type: ignore[misc]
        if (
            not remote_book.chapters
            or str(remote_book.title or "").strip() in ("", "Unknown")
        ):
            raise ValueError(
                f"Book page returned no usable metadata/chapters: {url} "
                f"(title={remote_book.title!r}, chapters={len(remote_book.chapters)})"
                + _last_book_diagnosis(plugin)
            )

        async def _checkpoint() -> None:
            if checkpoint_cb is not None:
                await checkpoint_cb()

        await _checkpoint()

        book_title = self._safe_title(remote_book)
        author_name = self._safe_author(remote_book.author)
        is_r18 = (
            self._is_book_r18(source, remote_book)
            if is_r18_override is None
            else bool(is_r18_override)
        )

        candidate_tags = self._clean_sync_tags(
            [*remote_book.tags, *(discovery_tags or [])],
            book_title,
            author_name,
        )
        matched_tag = _matches_exclusion(candidate_tags, exclude_tags or [])
        candidate_categories = classify_category_names(
            candidate_tags,
            title=book_title,
            description=remote_book.description or "",
            is_r18=is_r18,
        )
        matched_category = _matches_exclusion(
            candidate_categories,
            exclude_categories or [],
        )
        if matched_tag or matched_category:
            return {
                "filtered": True,
                "filter_type": "tag" if matched_tag else "category",
                "filter_value": matched_tag or matched_category,
                "title": book_title,
                "author": author_name,
                "tags": candidate_tags,
                "categories": candidate_categories,
                "created_chapters": 0,
                "skipped_chapters": 0,
                "failed_chapters": [],
            }

        author = await self._get_or_create_author(author_name)
        author_id = author.id
        book, is_new = await self._get_or_create_book(
            source.id,
            author_id,
            remote_book,
            is_r18=is_r18,
            owner_id=source.owner_id,
        )
        previous_title = book.title
        previous_author = author_name
        if not is_new:
            if book.author_id and book.author_id != author_id:
                previous_author = await self._book_author_name(book.author_id)
                if author_name == "Unknown":
                    author_name = previous_author
                    author_id = book.author_id
            book.title = book_title
            book.author_id = author_id
        remote_cover_url = await self._persist_cover(book, plugin, remote_book)

        # Tags are re-derived on every sync: the source is the authority, so a
        # tag added by an earlier (buggy) extraction disappears on the next
        # sync instead of accumulating forever.  Only fall back to the stored
        # tags when the source produced nothing usable, so a transient parse
        # failure cannot wipe a book's tags.  Manual tags live in
        # ``book_custom_tags`` and are untouched by this.
        classification_tag = "r18" if is_r18 else "all-ages"
        source_tags = candidate_tags
        if not is_new:
            source_tags = self._clean_sync_tags(source_tags, previous_title, previous_author)
        if not source_tags:
            stored_tags = {
                str(tag).strip().lower()
                for tag in await self._book_tag_names(book.id)
                if tag and str(tag).strip().lower() not in ("all-ages", "r18")
            }
            source_tags = self._clean_sync_tags(
                stored_tags,
                book_title,
                author_name,
            )
            if not is_new:
                source_tags = self._clean_sync_tags(source_tags, previous_title, previous_author)
        await self._save_tags(
            book.id,
            sorted([*source_tags, classification_tag]),
        )
        # Existing books retain their format until we can weigh stored chapters.
        if is_new:
            book.kind = resolve_kind(source=source, labels=source_tags)
        book_kind = normalize_kind(book.kind)
        source_config = source.config
        request_interval = source_sync_interval(source)
        # Persist the book before chapter downloads so a later chapter failure
        # cannot leave chapters pointing at an uncommitted book row.
        await self.db.commit()

        # One book = one storage directory.  Two books that share an author and
        # a title (several sources can carry the same book) used to write the
        # same ``000001.md``; the ref makes the second one use its own folder.
        storage_ref = BookRef(
            book_id=book.id,
            source_id=source.id,
            source_book_id=str(remote_book.source_book_id or ""),
        )
        self.storage.write_metadata(
            author_name,
            book_title,
            {
                "book_id": book.id,
                "source_id": source.id,
                "source_book_id": remote_book.source_book_id,
                "title": book_title,
                "author": author_name,
                "description": book.description,
                "status": book.status,
                "is_r18": is_r18,
                "tags": sorted(source_tags),
                "cover": book.cover,
                "cover_url": remote_cover_url,
            },
            ref=storage_ref,
        )

        book_tags = await self._book_tag_names(book.id)
        # ``custom_tags`` is a ``lazy="selectin"`` relationship that only
        # ``_get_or_create_book`` loads (and nothing at all loads on resync).
        # Reading it synchronously here raised ``MissingGreenlet`` once a
        # ``rollback()`` had expired the instance -- the "Crawl task ...
        # stopped: greenlet_spawn has not been called" failures.  Query the
        # names explicitly like ``_book_tag_names`` does.
        book_custom_tags = await self._book_custom_tag_names(self.db, book.id)
        index_tags = list(dict.fromkeys([*book_tags, *book_custom_tags]))
        book_id = book.id
        book_title = book.title
        book_is_r18 = book.is_r18
        book_author = author_name
        book_cover = book.cover
        book_source_book_id = book.source_book_id
        book_status = book.status
        source_id_value = source.id
        source_owner_id = source.owner_id
        book_description_full = book.description
        book_hidden = book.hidden
        book_values = {
            "source_id": source_id_value,
            "author_id": author_id,
            "source_book_id": book_source_book_id,
            "title": book_title,
            "cover": book_cover,
            "description": book_description_full,
            "status": book_status,
            "is_r18": book_is_r18,
            "owner_id": source_owner_id,
            "kind": book_kind,
        }
        try:
            from app.services.auto_categorize import AutoCategorizationService
            book_category_names = await AutoCategorizationService.categorize_book(self.db, book_id)
        except Exception as exc:
            await self.db.rollback()
            logger.warning("Could not recategorize book {}: {}", book_id, describe_error(exc))
            book_category_names = await self._book_category_names(book_id)
        # ``is_public`` is a user action, never synced from the source, so it
        # has to be read back from the stored row -- writing the doc without it
        # would silently un-share a book the owner made public.
        book_is_public = bool(await self._book_column(book_id, Book.is_public))
        # The Meilisearch client is synchronous; run it off the event loop so
        # a slow engine cannot stall every other sync in this process.
        await asyncio.to_thread(search_service.index_book, {
            "id": book_id,
            "title": book_title,
            "author": author_name,
            "description": book_description_full or "",
            "status": book_status or "",
            "source_id": source_id_value,
            "author_id": author_id,
            "is_r18": book_is_r18,
            "owner_id": source_owner_id,
            "is_public": book_is_public,
            "tags": index_tags,
            "category_names": book_category_names,
            "kind": book_kind,
        })

        image_book = SimpleNamespace(id=book_id, kind=book_kind)
        created = 0
        skipped = 0
        total = len(remote_book.chapters)
        failed_chapters: list[dict] = []

        async def _report_progress(
            remote_chapter,
            images_done: int = 0,
            images_total: int = 0,
            content_pages_done: int = 0,
        ) -> None:
            """Publish this book's progress to the crawl task row.

            ``images_done``/``images_total`` describe the image album of the
            chapter being downloaded right now. ``content_pages_done`` tracks
            paginated chapter fetches before their embedded images are saved.
            Both stages can be the only progress for a long-running comic.
            """
            if progress_cb is not None:
                await progress_cb({
                    "book_id": book_id,
                    "book_title": book_title,
                    "chapter_number": remote_chapter.chapter_number,
                    "chapter_title": remote_chapter.title,
                    "created_chapters": created,
                    "skipped_chapters": skipped,
                    "failed_chapters": len(failed_chapters),
                    "total_chapters": total,
                    "images_done": images_done,
                    "images_total": images_total,
                    "content_pages_done": content_pages_done,
                })

        refresh_rows: dict[str, str] = {}
        repair_ids: set[str] = set()
        synopsis_catalog = await self._synopses.for_owner(self.db, source_owner_id)
        if not book_hidden:
            self._synopses.remember(synopsis_catalog, book_title, book_description_full)
        existing_source_ids = await self._reconcile_chapter_ids(
            book_id,
            remote_book.chapters,
            gallery_size=len(getattr(plugin, "_chapter_image_manifest", []) or []),
            refetch_content=refetch_content,
            refresh_rows=refresh_rows,
            repair_ids=repair_ids,
            synopsis_catalog=synopsis_catalog,
            book_title=book_title,
        )
        repaired = 0

        missing_chapters = [
            remote_chapter
            for remote_chapter in remote_book.chapters
            if remote_chapter.source_chapter_id not in existing_source_ids
        ]
        for remote_chapter in remote_book.chapters:
            if remote_chapter.source_chapter_id in existing_source_ids:
                skipped += 1
                await _report_progress(remote_chapter)

        await _checkpoint()

        concurrency = self._chapter_concurrency(
            config,
            request_interval,
        )
        chapters_to_fetch = iter(missing_chapters)
        results_queue = asyncio.Queue(maxsize=concurrency * 2)

        async def _fetch_chapter(remote_chapter):
            async def _report_content_pages(pages_done: int) -> None:
                await _report_progress(
                    remote_chapter,
                    content_pages_done=pages_done,
                )

            async def _report_images(handled: int, images_total: int) -> None:
                await _report_progress(
                    remote_chapter,
                    images_done=handled,
                    images_total=images_total,
                )

            chapter_db_id = refresh_rows.get(remote_chapter.source_chapter_id) or str(uuid4())
            try:
                content = await self._fetch_chapter_with_retry(
                    plugin,
                    remote_chapter,
                    progress_cb=_report_content_pages,
                )
                content = await self._process_content_images(
                    image_book,
                    chapter_db_id,
                    content,
                    remote_chapter.url,
                    plugin,
                    image_progress_cb=_report_images,
                )
                return (remote_chapter, content, None, chapter_db_id)
            except Exception as exc:
                return (remote_chapter, None, exc, chapter_db_id)

        async def _producer() -> None:
            for remote_chapter in chapters_to_fetch:
                await results_queue.put(await _fetch_chapter(remote_chapter))

        remaining = len(missing_chapters)
        # Make sure the book row really exists before chapter inserts begin.
        book_row_verified = await self._ensure_book_row(book_id, book_values)
        consecutive_blocked = 0
        consecutive_transient = 0
        # Chapter rows are committed in batches (``SYNC_CHAPTER_COMMIT_BATCH``):
        # a commit is a WAL fsync, and one per chapter cost thousands of them per
        # book on the 4-core NAS, competing with the crawler's own writes.  The
        # search documents are buffered until the commit that makes their rows
        # durable, so the index never carries a chapter the database lacks.
        commit_batch = max(
            1, int(getattr(settings, "SYNC_CHAPTER_COMMIT_BATCH", 25) or 25)
        )
        file_batch = ChapterFileBatch()
        pending_chapters = []
        pending_index_docs: list[dict] = []
        max_consecutive_transient = max(
            1,
            int(
                getattr(
                    settings,
                    "SYNC_MAX_CONSECUTIVE_CHAPTER_FAILURES",
                    5,
                )
            ),
        )
        async def commit_chapters() -> None:
            nonlocal created, repaired
            await self._flush_chapter_writes(pending_index_docs, file_batch)
            created += len(pending_chapters)
            repaired += sum(ch.source_chapter_id in repair_ids for ch in pending_chapters)
            pending_chapters.clear()

        async def rollback_chapters(exc: BaseException) -> None:
            nonlocal book_row_verified
            affected = list(pending_chapters)
            pending_chapters.clear()
            pending_index_docs.clear()
            try:
                file_batch.finish(committed=False)
            finally:
                await self.db.rollback()
            book_row_verified = False
            for ch in affected:
                failed_chapters.append({
                    "chapter_number": ch.chapter_number, "title": ch.title,
                    "url": ch.url, "error": describe_error(exc)[:300],
                })
            logger.warning("Could not persist {} chapters of book {}: {}",
                           len(affected), book_id, describe_error(exc))

        producers = [
            asyncio.create_task(_producer())
            for _ in range(min(concurrency, remaining))
        ]
        try:
            while remaining > 0:
                remote_chapter, content, error, chapter_db_id = await results_queue.get()
                remaining -= 1
                await _checkpoint()
                if error is not None:
                    if isinstance(error, core_transient.UpstreamBackoff):
                        raise error
                    if self._is_infrastructure_error(error):
                        # Same rule as the book-level counters below: our own
                        # stack failing (pool exhaustion, rolled-back session)
                        # is recorded but counts toward nothing -- it must not
                        # trip either the anti-crawl abort or the transient
                        # abort of this book.
                        failed_chapters.append({
                            "chapter_number": remote_chapter.chapter_number,
                            "title": remote_chapter.title,
                            "url": remote_chapter.url,
                            "error": describe_error(error)[:300],
                        })
                        logger.warning(
                            "Infrastructure failure syncing chapter {} ({}): {}",
                            remote_chapter.title,
                            remote_chapter.url,
                            describe_error(error),
                        )
                        await _report_progress(remote_chapter)
                        continue
                    if self._is_upstream_blocked(error):
                        consecutive_transient = 0
                        consecutive_blocked += 1
                        failed_chapters.append({
                            "chapter_number": remote_chapter.chapter_number,
                            "title": remote_chapter.title,
                            "url": remote_chapter.url,
                            "error": describe_error(error)[:300],
                        })
                        logger.warning(
                            "Chapter {} blocked by anti-bot/captcha ({}): {}",
                            remote_chapter.title,
                            remote_chapter.url,
                            describe_error(error),
                        )
                        await _report_progress(remote_chapter)
                        if consecutive_blocked >= 5:
                            raise RuntimeError(
                                "同步被网站反爬拦截（验证码/限流页面），已连续 "
                                f"{consecutive_blocked} 章失败。请稍后重试、更换代理节点，"
                                "或检查源站是否要求人工验证。"
                            ) from error
                        continue
                    consecutive_blocked = 0
                    if self._is_transient_chapter_error(error):
                        consecutive_transient += 1
                    else:
                        consecutive_transient = 0
                    failed_chapters.append({
                        "chapter_number": remote_chapter.chapter_number,
                        "title": remote_chapter.title,
                        "url": remote_chapter.url,
                        "error": describe_error(error)[:300],
                    })
                    logger.warning(
                        "Failed to sync chapter {} ({}): {}",
                        remote_chapter.title,
                        remote_chapter.url,
                        describe_error(error),
                    )
                    await _report_progress(remote_chapter)
                    if consecutive_transient >= max_consecutive_transient:
                        raise RuntimeError(
                            "上游/代理暂时不可用（网络错误，例如 Cloudflare "
                            f"520/5xx），已连续 {consecutive_transient} 章失败；"
                            "已停止同步这本书，请检查代理节点或等待源站恢复后重新同步。"
                        ) from error
                    continue

                consecutive_blocked = 0
                consecutive_transient = 0
                # Some sources put the site's "related works" grid inside the
                # chapter container: the body is then nothing but other books'
                # covers.  Writing it would make a junk book that looks read-
                # able; skip the chapter instead and say so.
                rejection = await self._replacement_error(content, book_title, remote_chapter.title, synopsis_catalog)
                if rejection:
                    logger.warning("第 {} 章《{}》未写入：{}", remote_chapter.chapter_number, remote_chapter.title, rejection)
                    failed_chapters.append({
                        "chapter_number": remote_chapter.chapter_number,
                        "title": remote_chapter.title,
                        "url": remote_chapter.url,
                        "error": rejection,
                    })
                    await _report_progress(remote_chapter)
                    continue

                pending_chapters.append(remote_chapter)
                try:
                    if not book_row_verified:
                        if not await self._ensure_book_row(book_id, book_values):
                            raise SQLAlchemyError("Book row is missing and could not be restored")
                        book_row_verified = True
                    if remote_chapter.source_chapter_id in refresh_rows:
                        chapter = await self.db.get(Chapter, chapter_db_id, with_for_update=True, populate_existing=True)
                        if chapter is None:
                            raise SQLAlchemyError("Chapter was removed during refresh")
                    else:
                        chapter = Chapter(id=chapter_db_id, book_id=book_id)
                        self.db.add(chapter)
                    content_path, content_hash = self.storage.write_chapter(
                        author_name, book_title, remote_chapter.chapter_number,
                        remote_chapter.title, content, ref=storage_ref,
                        chapter_id=chapter_db_id, previous_path=chapter.content_path,
                        previous_hash=chapter.hash, batch=file_batch,
                    )
                    if remote_chapter.source_chapter_id in refresh_rows and chapter.hash != content_hash:
                        await self._invalidate_chapter_analysis(chapter_db_id)
                    chapter.chapter_number = remote_chapter.chapter_number
                    chapter.source_chapter_id = remote_chapter.source_chapter_id
                    chapter.title = remote_chapter.title
                    chapter.content_path = content_path
                    chapter.hash = content_hash
                    await self.db.flush()
                    pending_index_docs.append({
                        "id": chapter.id,
                        "book_id": book_id,
                        "title": chapter.title or "",
                        "chapter_number": chapter.chapter_number,
                        "content": self._strip_content_images(content)[
                            : search_service.content_index_limit()
                        ],
                        "book_title": book_title,
                        "book_author": book_author,
                        "tags": index_tags,
                        "category_names": book_category_names,
                        "is_r18": book_is_r18,
                        "owner_id": source_owner_id,
                        "is_public": book_is_public,
                        "kind": book_kind,
                    })
                    if len(pending_chapters) >= commit_batch:
                        await commit_chapters()
                except TruncatedChapterError as exc:
                    # The storage check runs before either revision is written.
                    pending_chapters.pop()
                    failed_chapters.append({
                        "chapter_number": remote_chapter.chapter_number, "title": remote_chapter.title,
                        "url": remote_chapter.url, "error": str(exc),
                    })
                except asyncio.CancelledError as exc:
                    await rollback_chapters(exc)
                    raise
                except Exception as exc:
                    await rollback_chapters(exc)
                await _report_progress(remote_chapter)
        finally:
            for producer in producers:
                producer.cancel()
            await asyncio.gather(*producers, return_exceptions=True)
            tail_chapter = pending_chapters[-1] if pending_chapters else None
            if pending_chapters:
                try:
                    await commit_chapters()
                except asyncio.CancelledError as exc:
                    await rollback_chapters(exc)
                    raise
                except Exception as exc:
                    await rollback_chapters(exc)
            await asyncio.to_thread(search_service.flush_chapters)

        if tail_chapter is not None:
            await _report_progress(tail_chapter)
        final_kind = await self._resolve_synced_kind(book_id, source_config, source_tags, book_kind, book_title, synopsis_catalog)
        if final_kind != book_kind:
            await self.db.commit()
            await refresh_search_kinds([book_id])
        if source_owner_id is None:
            await self._handle_global_r18_conflicts(book_id)
        if not is_new:
            try:
                await asyncio.to_thread(search_service.reconcile_chapter_labels, book_id, index_tags, book_category_names)
            except Exception as exc:
                logger.warning("Could not refresh indexed chapter labels for {}: {}", book_id, describe_error(exc))
        logger.info(
            "Sync complete book={} created={} skipped={} failed={}",
            book_id,
            created,
            skipped,
            len(failed_chapters),
        )

        return {
            "book_id": book_id,
            "created_chapters": created,
            "skipped_chapters": skipped,
            "failed_chapters": failed_chapters,
            # Text replaced because it was unusable -- it belonged to another
            # book that shared the same storage directory, or the page came
            # back with only images/ads (see codex-handoff 56/58).  A repair
            # the admin did not ask for, so it is reported, not silent.
            "repaired_chapters": repaired,
        }

    async def _replacement_error(self, content: str, book_title: str, chapter_title: str, catalog: dict) -> str:
        if not content.strip():
            return "抓取正文为空，已跳过写入"
        if chapter_text_is_blocked(content):
            return "抓取结果包含站点验证提示，已跳过写入"
        if foreign_synopsis(content, book_title, chapter_title, catalog):
            return "抓取内容匹配其他书籍的简介，已跳过写入"
        wall_hits = await self._cover_wall_hits(content, book_title)
        return "正文仅包含其他书籍的封面：" + "、".join(wall_hits[:3]) if wall_hits else ""

    async def _cover_wall_hits(self, content: str, book_title: str) -> list[str]:
        labels = cover_wall_labels(content)
        if not labels:
            return []
        return match_cover_titles(labels, await self._library_titles(), book_title)

    async def _library_titles(self) -> set[str]:
        """Every book title in the library (loaded once per sync run)."""
        if self._other_book_titles is None:
            rows = await self.db.scalars(select(Book.title))
            self._other_book_titles = {normalize_title(title) for title in rows.all() if title}
        return self._other_book_titles

    async def _flush_chapter_writes(self, pending_index_docs: list[dict], file_batch: ChapterFileBatch | None = None) -> None:
        """Commit the chapter rows written since the last batch, then index them.

        Order matters: a search document must never describe a chapter the
        database does not have, so the commit comes first.  Any error propagates
        to the caller, which rolls back and drops ``pending_index_docs`` -- the
        rows are gone, so their documents must not be indexed.

        Committing per chapter (the older behaviour) was one WAL fsync per
        chapter, thousands of them per book; batching keeps the same durability
        guarantee at the batch boundary (a crash loses at most
        ``SYNC_CHAPTER_COMMIT_BATCH - 1`` fetched chapters, whose files are
        already on disk and whose rows the next sync simply refetches).
        """
        await self.db.commit()
        if file_batch is not None:
            file_batch.finish(committed=True)
        docts = list(pending_index_docs)
        pending_index_docs.clear()
        for doc in docts:
            try:
                await asyncio.to_thread(search_service.buffer_chapter, doc)
            except Exception as exc:
                # The row is committed and the file is on disk; the search index
                # is the only thing behind, and the next sync (or a rebuild) puts
                # the document there.  Failing the whole book over it -- which is
                # what a per-chapter ``buffer_chapter`` error used to do, by
                # landing in the chapter-failure handler after the commit -- is
                # strictly worse.
                logger.warning(
                    "Chapter {} saved but not queued for the index: {}",
                    doc.get("id"),
                    describe_error(exc),
                )

    async def _get_or_create_author(self, name: str) -> Author:
        # Fallback for empty/missing author names from book sources
        safe_name = name.strip() if name and name.strip() else "Unknown"
        author = await self.db.scalar(select(Author).where(Author.name == safe_name))
        if author:
            return author
        author = Author(id=str(uuid4()), name=safe_name)
        self.db.add(author)
        await self.db.flush()
        return author

    async def _persist_cover(self, book, plugin, remote_book) -> str | None:
        """Download a remote cover, save it locally, and update book.cover."""
        cover_url = str(getattr(remote_book, "cover_url", "") or "").strip()
        if not cover_url or not self._is_http_url(cover_url):
            return None
        if len(cover_url) > 255 or "\n" in cover_url or " " in cover_url:
            # A source rule may return several img@src values joined together.
            # That is not a usable cover and cannot fit the books.cover column.
            return None

        fetch = getattr(plugin, "fetch_cover", None)
        if not callable(fetch):
            book.cover = cover_url
            return cover_url

        try:
            result = await fetch(cover_url)
            data = result[0] if isinstance(result, tuple) else result
        except Exception as exc:
            logger.warning("Failed to fetch cover {}: {}", cover_url, describe_error(exc))
            book.cover = cover_url
            return cover_url

        if not data:
            book.cover = cover_url
            return cover_url

        try:
            local_path = self.storage.save_cover(book.id, data)
        except Exception as exc:
            logger.warning("Failed to save cover for {}: {}", book.id, exc)
            book.cover = cover_url
            return cover_url

        book.cover = local_path
        return cover_url

    async def _get_or_create_book(
        self,
        source_id: str,
        author_id: str,
        remote_book,
        is_r18: bool = False,
        owner_id: str | None = None,
    ) -> tuple[Book, bool]:
        book = await self.db.scalar(
            select(Book)
            .options(selectinload(Book.tags))
            .where(
                Book.source_id == source_id,
                Book.source_book_id == remote_book.source_book_id,
            )
        )
        if book:
            # The caller also needs the earlier identity to clean legacy tags.
            if remote_book.description and remote_book.description.strip():
                book.description = remote_book.description
            if remote_book.status:
                book.status = self._clamp(remote_book.status, 32)
            book.is_r18 = is_r18
            book.owner_id = owner_id
            await self.db.flush()
            return book, False

        book = Book(
            id=str(uuid4()),
            source_id=source_id,
            author_id=author_id,
            source_book_id=self._clamp(
                str(remote_book.source_book_id or ""),
                255,
            ),
            title=self._safe_title(remote_book),
            description=remote_book.description,
            status=self._clamp(
                str(remote_book.status or ""),
                32,
            ) or None,
            is_r18=is_r18,
            owner_id=owner_id,
        )
        self.db.add(book)
        await self.db.flush()
        return book, True

    async def _reconcile_chapter_ids(
        self,
        book_id: str,
        remote_chapters: list,
        gallery_size: int = 0,
        refetch_content: bool = False,
        refresh_rows: dict[str, str] | None = None,
        repair_ids: set[str] | None = None,
        synopsis_catalog: dict[bytes, str] | None = None,
        book_title: str = "",
    ) -> set[str]:
        """Return existing source chapter ids, upgrading legacy numeric ids to URLs.

        Legado identifies chapters by their URL, while older NovelHub syncs
        stored the positional chapter number. Upgrading in place prevents a
        full re-download when the TOC order is stable.

        Chapters still in the TOC keep their database identity while their
        replacement is being fetched. Return only IDs safe to skip. Record
        the IDs of rows needing an update in refresh_rows. Drop obsolete
        navigation/duplicate rows separately.
        """
        rows = await self.db.scalars(
            select(Chapter).where(Chapter.book_id == book_id)
        )
        chapters = list(rows.all())
        by_id = {
            str(ch.source_chapter_id): ch
            for ch in chapters
            if ch.source_chapter_id is not None
        }
        by_number = {
            ch.chapter_number: ch
            for ch in chapters
            if ch.chapter_number is not None
        }

        remote_ids = {str(remote.source_chapter_id or "") for remote in remote_chapters}
        upgraded = False
        for remote_chapter in remote_chapters:
            source_id = str(remote_chapter.source_chapter_id or "")
            if not source_id or source_id in by_id:
                continue
            legacy = by_number.get(remote_chapter.chapter_number)
            if legacy is None:
                continue
            legacy_id = str(legacy.source_chapter_id or "")
            if legacy_id in remote_ids or (legacy_id and legacy_id != str(legacy.chapter_number)):
                continue
            if legacy.title and remote_chapter.title and (
                self._normalize_title_for_match(legacy.title) != self._normalize_title_for_match(remote_chapter.title)
            ):
                continue
            by_id.pop(legacy_id, None)
            upgraded = True
            legacy.source_chapter_id = source_id
            by_id[source_id] = legacy

        remote_titles = Counter(self._normalize_title_for_match(remote.title) for remote in remote_chapters)
        stored_titles = Counter(self._normalize_title_for_match(ch.title) for ch in chapters)
        # Sources sometimes change the entire chapter URL scheme. Reuse an
        # unambiguous reader ID, but change its source URL only after a good fetch.
        changed_urls: set[str] = set()
        if refresh_rows is not None:
            old_by_title = {
                self._normalize_title_for_match(ch.title): old_id
                for old_id, ch in by_id.items() if old_id not in remote_ids
            }
            for remote in remote_chapters:
                new_id = str(remote.source_chapter_id or "")
                title = self._normalize_title_for_match(remote.title)
                if (new_id and new_id not in by_id and title and title in old_by_title
                        and stored_titles[title] == remote_titles[title] == 1):
                    by_id[new_id] = by_id.pop(old_by_title[title])
                    changed_urls.add(new_id)
        remote_hosts = {
            urlparse(remote_chapter.url).netloc.lower()
            for remote_chapter in remote_chapters
            if urlparse(remote_chapter.url).scheme in ("http", "https")
        }
        # A book is "prose" when at least one stored chapter holds real text:
        # that is what tells an ad/cover-only page from a real manga gallery.
        # The verdict costs a read of every chapter file, so it is computed on
        # demand (only when a chapter actually looks image-only) and memoised --
        # a comic used to pay for the whole scan on every sync, on top of the
        # per-chapter health check.
        prose_verdict: list[bool] = []

        def book_has_prose() -> bool:
            if not prose_verdict:
                prose_verdict.append(any(
                    self._chapter_body_has_prose(chapter, synopsis_catalog=synopsis_catalog, book_title=book_title)
                    for chapter in by_id.values()
                    if chapter.content_path
                ))
            return prose_verdict[0]
        ready_ids = set(by_id)
        stale_ids: list[str] = []
        for source_id, chapter in by_id.items():
            if source_id in remote_ids:
                reason = ""
                if not refetch_content:
                    reason = self._stale_chapter_reason(chapter, book_has_prose=book_has_prose,
                                                        synopsis_catalog=synopsis_catalog, book_title=book_title)
                if refetch_content or reason or source_id in changed_urls or self._is_truncated_gallery(chapter, gallery_size):
                    ready_ids.discard(source_id)
                    if refresh_rows is not None:
                        refresh_rows[source_id] = chapter.id
                    if reason in ("foreign", "image-only", "synopsis") and repair_ids is not None:
                        repair_ids.add(source_id)
            elif (
                self._looks_like_junk_chapter(chapter, remote_hosts)
                or self._normalize_title_for_match(chapter.title) in remote_titles
            ):
                # A changed domain or repeated title alone cannot prove a
                # readable chapter is obsolete.
                if self._stale_chapter_reason(chapter):
                    stale_ids.append(source_id)
        for source_id in stale_ids:
            chapter = by_id.pop(source_id)
            await self._release_chapter_references(chapter.id)
            await self.db.delete(chapter)

        ready_ids.difference_update(stale_ids)
        moved = []
        for remote in remote_chapters:
            key = str(remote.source_chapter_id)
            if key in ready_ids and by_id[key].chapter_number != remote.chapter_number:
                by_id[key].chapter_number = remote.chapter_number
                moved.append({"id": by_id[key].id, "chapter_number": remote.chapter_number})
        await self.db.flush()
        if moved or upgraded or stale_ids:
            await self.db.commit()
        if moved:
            try:
                await asyncio.to_thread(search_service.update_chapter_numbers, moved)
            except Exception as exc:
                logger.warning("Could not update indexed chapter numbers for {}: {}", book_id, exc)
        return ready_ids

    async def _invalidate_chapter_analysis(self, chapter_id: str) -> None:
        from app.services.ai_insights import mark_chapter_insights_stale

        await self.db.execute(delete(ChapterEmbedding).where(ChapterEmbedding.chapter_id == chapter_id))
        await mark_chapter_insights_stale(self.db, chapter_id)

    async def _release_chapter_references(self, chapter_id: str) -> None:
        """Detach rows that point at a chapter about to be replaced.

        ``bookmarks`` / ``chapter_embeddings`` cascade on delete, but
        ``reading_progress`` (and the legacy ``book_versions`` table) do not,
        so deleting a stale chapter used to abort the whole book sync with a
        ForeignKeyViolation.  A progress row keeps the book and its position
        so "继续阅读" still lists the book; only the dangling pointer is
        cleared (the replacement chapter gets a new id anyway).
        """
        await self.db.execute(
            update(ReadingProgress)
            .where(ReadingProgress.chapter_id == chapter_id)
            .values(chapter_id=None)
        )
        await self.db.execute(
            delete(BookVersion).where(BookVersion.chapter_id == chapter_id)
        )
        await self.db.execute(
            delete(Bookmark).where(Bookmark.chapter_id == chapter_id)
        )
        # Saved AI analyses that quoted this chapter stay attached to the book
        # but are flagged: the text they were based on is about to be replaced,
        # so the model must re-check them against the new version.
        from app.services.ai_insights import mark_chapter_insights_stale

        await mark_chapter_insights_stale(self.db, chapter_id)

    def _chapter_has_real_content(self, chapter: Chapter) -> bool:
        """Return False for empty, anti-bot or foreign junk chapters so a
        re-sync refetches them."""
        return self._stale_chapter_reason(chapter) == ""

    def _stale_chapter_reason(
        self,
        chapter: Chapter,
        *,
        book_has_prose: bool | Callable[[], bool] = False,
        synopsis_catalog: dict[bytes, str] | None = None,
        book_title: str = "",
    ) -> str:
        """Why a stored chapter must be fetched again (``""`` when it is fine).

        The reason is a value, not just a boolean, so a caller can tell an
        expected repair (a junk/anti-bot chapter) from the collateral one this
        module owes the storage fix below -- and report the latter.

        ``book_has_prose`` says whether *any* chapter of this book holds real
        text.  An image-only chapter is a legit manga page in a gallery book,
        but in a book whose other chapters are prose it is the page's ads or
        recommendation covers (御宅屋 stored "投推荐票 …" pages and cover grids
        that way) and belongs in the same repair path.  It may be a callable so
        the (whole-book) verdict is only computed when a chapter actually looks
        image-only -- see ``_resolve_book_has_prose``.
        """
        # A missing storage path means the historical chapter has no usable
        # body. Treat it as stale so the next sync can fetch it again.
        if not chapter.content_path:
            return "missing"
        try:
            content = self.storage.read_chapter(chapter.content_path)
        except Exception:
            # A missing or unreadable storage object must be fetched again.
            return "missing"
        if not isinstance(content, str):
            return "missing"
        if not self._chapter_file_is_ours(chapter, content):
            # Another book overwrote this file (books used to share one
            # directory per author+title), so what is on disk is not this
            # chapter's text.  Refetch instead of serving the wrong book.
            return "foreign"
        if synopsis_catalog is not None and foreign_synopsis(content, book_title, chapter.title, synopsis_catalog):
            return "synopsis"
        has_image_refs = bool(
            re.search(
                r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*\bsrc=",
                content,
                re.IGNORECASE,
            )
        )
        body = re.sub(r"^#.*(?:\r?\n|$)", "", content, flags=re.M).strip()
        # Markdown image references and HTML wrappers are not chapter text.
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
        body = re.sub(r"<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", body).strip()
        if chapter_text_is_blocked(body):
            return "blocked"
        # A chapter stored as one bare image URL is the old broken manga
        # fallback, not readable content.  Let a re-sync replace it with the
        # image-page chain (markdown/HTML image references).
        if body and re.fullmatch(r"(?:https?://\S+\s*)+", body):
            return "bare-url"
        if len(body) < 20:
            # Image-only manga chapters are valid even though stripping their
            # image references leaves no text.
            if not has_image_refs:
                return "empty"
            if not self._resolve_book_has_prose(book_has_prose):
                return ""
            return "image-only"
        return ""

    @staticmethod
    def _resolve_book_has_prose(verdict: bool | Callable[[], bool]) -> bool:
        """Resolve the "does this book hold any prose" verdict on demand.

        ``_chapter_body_has_prose`` reads chapter files, and computing the
        verdict up front meant a comic book paid for a scan of *every* chapter
        file on every sync (the eager ``any()`` only short-circuits for prose
        books).  Callers now pass a memoising callable, so the scan happens only
        when a stored chapter actually looks image-only, and at most once per
        book.
        """
        return bool(verdict() if callable(verdict) else verdict)

    def _chapter_body_has_prose(
        self,
        chapter: Chapter,
        content: str | None = None,
        *,
        synopsis_catalog: dict[bytes, str] | None = None,
        book_title: str = "",
    ) -> bool:
        """Whether a stored chapter holds readable text rather than only images.

        Same normalization as the health check: the ``#`` title line, markdown
        image references and HTML wrappers are not prose.  Pass ``content`` when
        the caller has already read the file.
        """
        if content is None:
            if not chapter.content_path:
                return False
            try:
                content = self.storage.read_chapter(chapter.content_path)
            except Exception:
                return False
        if not isinstance(content, str):
            return False
        body = re.sub(r"^#.*(?:\r?\n|$)", "", content, flags=re.M)
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
        body = re.sub(r"<[^>]+>", " ", body)
        if len(re.sub(r"\s+", " ", body).strip()) < 20 or chapter_text_is_blocked(body):
            return False
        return (self._chapter_file_is_ours(chapter, content) and
                (synopsis_catalog is None or not foreign_synopsis(content, book_title, chapter.title, synopsis_catalog)))

    def _chapter_file_is_ours(
        self,
        chapter: Chapter,
        content: str | None = None,
    ) -> bool:
        """Whether the file at ``content_path`` is still this chapter's text.

        ``write_chapter`` returns the sha256 of the markdown it wrote and the
        chapter row keeps it, so recomputing the digest answers the question
        exactly: a mismatch means some other book's sync replaced the file (the
        shared-directory era), never a heuristic guess.  Rows without a stored
        digest (imported data, older schemas) are trusted.

        ``content`` is the text the caller already read; the digest is then
        taken from it instead of reading the file a second time (a routine sync
        verifies every chapter of every book it syncs).
        """
        expected = str(getattr(chapter, "hash", "") or "").strip()
        if not expected or not chapter.content_path:
            return True
        if content is not None:
            return text_digest(content) == expected
        digest = self.storage.chapter_digest(chapter.content_path)
        if digest is None:
            # Unreadable: let the caller's read attempt decide.
            return True
        return digest == expected

    def _is_truncated_gallery(self, chapter: Chapter, gallery_size: int) -> bool:
        """Whether a stored album chapter is the old "exactly N images" cut.

        The gallery walk used to stop as soon as it held as many images as the
        source's ``imgInfoList`` manifest -- one album *index* page, 12 entries
        for 绅士漫画 -- so every album was truncated to that length.  A stored
        image-only chapter with that exact ceiling is the artifact, and a
        re-sync replaces it with the whole album.  Albums that really are that
        short simply come back unchanged.
        """
        if gallery_size <= 1 or not chapter.content_path:
            return False
        try:
            content = self.storage.read_chapter(chapter.content_path)
        except Exception:
            return False
        if not isinstance(content, str) or not content:
            return False
        body = re.sub(r"^#.*(?:\r?\n|$)", "", content, flags=re.M)
        body = re.sub(r"<img\b[^>]*>", "", body)
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
        if body.strip():
            # Text next to the pictures: a normal chapter, not a photo album.
            return False
        count = len(CONTENT_IMAGE_RE.findall(content))
        return 0 < count <= gallery_size

    @staticmethod
    def _looks_like_junk_chapter(chapter: Chapter, remote_hosts: set[str]) -> bool:
        title = SyncService._normalize_title_for_match(chapter.title)
        if title in JUNK_CHAPTER_TITLES:
            return True
        url = str(chapter.source_chapter_id or "")
        if not url.startswith(("http://", "https://")):
            # Legacy numeric IDs are kept unless their title is obvious junk.
            return False
        parsed = urlparse(url)
        if remote_hosts and parsed.netloc.lower() not in remote_hosts:
            return True
        path = parsed.path.lower()
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 2:
            return True
        return any(segment in JUNK_CHAPTER_PATH_SEGMENTS for segment in segments)

    async def _ensure_book_row(self, book_id: str, book_values: dict) -> bool:
        """Restore a missing book row before chapter inserts."""
        try:
            existing = await self.db.scalar(
                select(Book.id).where(Book.id == book_id)
            )
            if existing is not None:
                return True
            self.db.add(Book(id=book_id, **book_values))
            await self.db.commit()
            return True
        except SQLAlchemyError:
            await self.db.rollback()
            return False

    async def sync_bookshelf(self, source_id: str) -> dict:
        """Sync all books from a user's bookshelf."""
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")

        stored_cookie = await load_source_cookie(source_id)
        if stored_cookie is None:
            raise ValueError(f"No cookie found for source '{source_id}'")

        config = source.config if source.plugin_name == 'yuedu' else None
        plugin = self._source_plugin(source)
        cookie_data = safe_decrypt_cookie(stored_cookie)
        plugin.set_cookie(cookie_data)

        shelf_books = await plugin.fetch_bookshelf(cookie_data)
        logger.info("Found {} books on bookshelf for source={}", len(shelf_books), source_id)

        results = []
        for shelf_book in shelf_books:
            if not self._is_http_url(shelf_book.url):
                results.append({
                    "url": shelf_book.url,
                    "status": "skipped",
                    "error": "Unsupported URL",
                    "created_chapters": 0,
                    "skipped_chapters": 0,
                    "failed_chapters": [],
                })
                continue
            try:
                result = await self.sync_book(source_id, shelf_book.url)
                if source.owner_id and result.get("book_id"):
                    existing_favorite = await self.db.scalar(
                        select(BookFavorite).where(
                            BookFavorite.user_id == source.owner_id,
                            BookFavorite.book_id == result["book_id"],
                        )
                    )
                    if existing_favorite is None:
                        self.db.add(BookFavorite(
                            id=str(uuid4()),
                            user_id=source.owner_id,
                            book_id=result["book_id"],
                        ))
                        await self.db.commit()
                results.append({
                    "book_id": result["book_id"],
                    "status": "ok",
                    "created_chapters": result.get("created_chapters", 0),
                    "skipped_chapters": result.get("skipped_chapters", 0),
                    "failed_chapters": result.get("failed_chapters", []),
                })
            except Exception as exc:
                logger.opt(exception=exc).warning(
                    "Failed to sync shelf book {}", shelf_book.url
                )
                await self.db.rollback()
                results.append({
                    "url": shelf_book.url,
                    "status": "failed",
                    "error": describe_error(exc),
                    "created_chapters": 0,
                    "skipped_chapters": 0,
                    "failed_chapters": [],
                })

        return {"source_id": source_id, "total": len(shelf_books), "results": results}

    async def _save_tags(self, book_id: str, tag_names: list[str]) -> None:
        """Create or get tags and associate them with the book."""
        tag_repo = TagRepository(self.db)
        # Remove old tag associations
        old_tags = await self.db.scalars(
            select(BookTag).where(BookTag.book_id == book_id)
        )
        for bt in old_tags:
            await self.db.delete(bt)

        seen: set[str] = set()
        for name in tag_names:
            name = name.strip().lower()
            if not name or name in seen:
                continue
            seen.add(name)
            tag = await tag_repo.get_or_create(name)
            bt = BookTag(book_id=book_id, tag_id=tag.id)
            self.db.add(bt)
        await self.db.flush()
    async def resync_book(self, book_id: str, refetch_content: bool = False) -> dict:
        """Re-sync a book already in the library from its source.

        ``refetch_content`` refreshes every chapter still in the source TOC
        and keeps each chapter's reader-facing identity.  That is the repair for chapters whose stored text
        is wrong but looks healthy -- content saved by an older build, or by
        another book that shared its directory before the storage fix.
        """
        book = await self.db.get(Book, book_id)
        if book is None:
            raise ValueError("Book not found")
        if not book.source_id or not book.source_book_id:
            raise ValueError("Book has no source reference")
        source, plugin = await self._book_plugin(book)
        url = self._book_url(book, source, plugin)
        return await self.sync_book(
            book.source_id,
            url,
            refetch_content=refetch_content,
        )

    async def resync_chapter(self, chapter_id: str) -> dict:
        """Re-fetch and overwrite a single chapter from its book source."""
        chapter = await self.db.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("Chapter not found")
        book = await self.db.get(Book, chapter.book_id)
        if book is None:
            raise ValueError("Book not found")
        if not book.source_id or not book.source_book_id:
            raise ValueError("Book has no source reference")

        source, plugin = await self._book_plugin(book)
        url = self._book_url(book, source, plugin)
        remote_book = await plugin.fetch_book(url)
        remote_chapter = self._match_remote_chapter(chapter, remote_book.chapters)
        if remote_chapter is None:
            raise ValueError("Chapter not found in source TOC")

        content = await self._fetch_chapter_with_retry(plugin, remote_chapter)
        content = await self._process_content_images(
            book,
            chapter.id,
            content,
            remote_chapter.url,
            plugin,
        )
        book_id = book.id
        book_is_r18 = book.is_r18
        book_source_id = str(book.source_id or "")
        book_source_book_id = str(book.source_book_id or "")
        # ``tag_names`` / ``category_names`` / ``author_name`` are synchronous
        # ORM relationship reads that raise ``MissingGreenlet`` on an expired
        # instance -- the resync-chapter "greenlet_spawn has not been called"
        # failures.  The chapter fetch above never touches this session, but a
        # concurrent ``rollback()`` on the same session expires these anyway,
        # so read everything through explicit queries up front.
        book_author = await self._book_author_name(book.author_id)
        book_tags = await self._book_tag_names(book.id)
        book_custom_tags = await self._book_custom_tag_names(self.db, book.id)
        index_tags = list(dict.fromkeys([*book_tags, *book_custom_tags]))
        book_category_names = await self._book_category_names(book.id)
        author_name = book_author
        book_title = book.title
        book_kind_value = book.kind
        catalog = await self._synopses.for_owner(self.db, book.owner_id)
        rejection = await self._replacement_error(content, book_title, remote_chapter.title, catalog)
        if rejection:
            raise ValueError(rejection)
        # Re-read the current revision under the same lock used by book-wide
        # refreshes, after the potentially slow network requests are finished.
        await self.db.refresh(chapter, with_for_update=True)
        file_batch = ChapterFileBatch()
        try:
            content_path, content_hash = self.storage.write_chapter(
                author_name,
                book_title,
                remote_chapter.chapter_number,
                remote_chapter.title,
                content,
                batch=file_batch, chapter_id=chapter_id, previous_path=chapter.content_path, previous_hash=chapter.hash,
                ref=BookRef(
                    book_id=book_id,
                    source_id=book_source_id,
                    source_book_id=book_source_book_id,
                ),
            )

            if chapter.hash != content_hash:
                await self._invalidate_chapter_analysis(chapter.id)
            chapter.title = remote_chapter.title
            chapter.chapter_number = remote_chapter.chapter_number
            chapter.source_chapter_id = remote_chapter.source_chapter_id
            chapter.content_path = content_path
            chapter.hash = content_hash
            current_kind = normalize_kind(book_kind_value)
            final_kind = await self._resolve_synced_kind(book_id, source.config, book_tags, current_kind, book_title, catalog)
            await self.db.commit()
        except BaseException:
            try:
                file_batch.finish(committed=False)
            finally:
                await self.db.rollback()
            raise
        file_batch.finish(committed=True)

        await asyncio.to_thread(search_service.index_chapter, {
            "id": chapter.id,
            "book_id": book_id,
            "title": chapter.title or "",
            "chapter_number": chapter.chapter_number,
            "content": self._strip_content_images(content)[
                : search_service.content_index_limit()
            ],
            "book_title": book_title,
            "book_author": book_author,
            "tags": index_tags,
            "category_names": book_category_names,
            "is_r18": book_is_r18,
            "owner_id": book.owner_id or None,
            "is_public": bool(book.is_public),
            "kind": final_kind,
        })
        if final_kind != current_kind:
            await refresh_search_kinds([book_id])

        return {
            "chapter_id": chapter.id,
            "book_id": book_id,
            "updated": True,
            "title": chapter.title,
            "chapter_number": chapter.chapter_number,
            "content_path": content_path,
            "content_length": len(content),
        }

    async def _resolve_synced_kind(self, book_id: str, source_config, labels, current: str,
                                   book_title: str, catalog: dict[bytes, str]) -> str:
        override = await self._book_column(book_id, Book.kind_override)
        if override:
            return normalize_kind(override)

        def unusable(text: str) -> bool:
            heading, _, body = text.partition("\n")
            title = heading[1:].strip() if heading.startswith("#") else ""
            return chapter_text_is_blocked(body if title else text) or foreign_synopsis(text, book_title, title, catalog)

        kind = await classify_stored_book(
            self.db, self.storage, book_id, source=source_config, labels=labels, current=current, ignore_content=unusable,
        )
        if kind != current:
            await self.db.execute(
                update(Book)
                .where(Book.id == book_id, Book.kind_override.is_(None), Book.kind != kind)
                .values(kind=kind)
            )
        return normalize_kind(await self._book_column(book_id, Book.kind) or current)

    async def _book_plugin(self, book: Book) -> tuple[Source, object]:
        """Load a book's source and plugin instance for re-sync operations."""
        source = await self.db.get(Source, book.source_id)
        if source is None:
            raise ValueError("Source not found")
        if not source.enabled:
            raise ValueError("Source disabled")
        plugin = self._source_plugin(source)
        stored_cookie = await load_source_cookie(book.source_id)
        if stored_cookie:
            plugin.set_cookie(safe_decrypt_cookie(stored_cookie))
        return source, plugin

    @staticmethod
    def _book_url(book: Book, source: Source, plugin) -> str:
        """Construct the remote book URL used by re-sync operations."""
        if hasattr(plugin, "build_book_url"):
            return plugin.build_book_url(book.source_book_id)
        cfg = plugin.config if hasattr(plugin, "config") else None
        if cfg and hasattr(cfg, "book_url"):
            return cfg.base_url + cfg.book_url.format(book_id=book.source_book_id)
        url = source.url or ""
        if not url:
            raise ValueError("Cannot determine book URL for re-sync")
        return urljoin(url.rstrip("/") + "/", book.source_book_id)

    @staticmethod
    def _match_remote_chapter(chapter: Chapter, remote_chapters: list):
        """Match a URL or a unique title. Position is a fallback for legacy IDs."""
        for remote_chapter in remote_chapters:
            if (
                chapter.source_chapter_id
                and remote_chapter.source_chapter_id == chapter.source_chapter_id
            ):
                return remote_chapter
        normalized = SyncService._normalize_title_for_match(chapter.title)
        same_title = [remote for remote in remote_chapters if normalized
                      and SyncService._normalize_title_for_match(remote.title) == normalized]
        if len(same_title) == 1:
            return same_title[0]
        if (not chapter.source_chapter_id or str(chapter.source_chapter_id) == str(chapter.chapter_number)) and (
            not normalized or same_title
        ):
            return next((remote for remote in (same_title or remote_chapters)
                         if remote.chapter_number == chapter.chapter_number), None)
        return None

    async def discover_and_sync(
        self,
        source_id: str,
        url: str | None = None,
        page: int = 1,
        sync: bool = True,
    ) -> dict:
        """Discover books from a source's explore/catalog page and optionally sync them.

        Uses the plugin's discover_books method if available. Falls back
        to returning an empty list for plugins that don't implement it.
        """
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")

        config = source.config if source.plugin_name == "yuedu" else None
        plugin = self._source_plugin(source)

        stored_cookie = await load_source_cookie(source_id)
        if stored_cookie:
            plugin.set_cookie(safe_decrypt_cookie(stored_cookie))

        if not hasattr(plugin, "discover_books"):
            return {
                "source_id": source_id,
                "books_found": 0,
                "books_synced": 0,
                "details": [],
            }

        shelf_books = await plugin.discover_books(url=url, page=page)
        books_found = len(shelf_books)
        books_synced = 0
        details = []

        if sync:
            for sb in shelf_books:
                if not self._is_http_url(sb.url):
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": False,
                        "error": "Unsupported URL",
                    })
                    continue
                try:
                    result = await self.sync_book(
                        source_id,
                        sb.url,
                        discovery_tags=sb.tags,
                    )
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": True,
                        "book_id": result.get("book_id"),
                    })
                    books_synced += 1
                except Exception as exc:
                    await self.db.rollback()
                details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": False,
                        "error": describe_error(exc),
                    })
        else:
            for sb in shelf_books:
                details.append({
                    "title": sb.title,
                    "author": sb.author,
                    "url": sb.url,
                    "synced": False,
                })

        return {
            "source_id": source_id,
            "books_found": books_found,
            "books_synced": books_synced,
            "details": details,
        }

    async def discover_and_sync_all(
        self,
        source_id: str,
        url: str | None = None,
        max_pages: int = 200,
        sync: bool = True,
        progress_cb: Callable[[int, int, int, int], Awaitable[None]] | None = None,
        chapter_progress_cb: Callable[[dict], Awaitable[None]] | None = None,
        before_step: Callable[[], Awaitable[None]] | None = None,
        checkpoint_cb: Callable[[], Awaitable[None]] | None = None,
        start_page: int = 1,
        page_batch_size: int = 0,
        exclude_tags: list[str] | None = None,
        exclude_categories: list[str] | None = None,
        book_concurrency: int | None = None,
        completed_page_urls: list[str] | None = None,
        backoff_cb: Callable[[dict, list[str]], None] | None = None,
    ) -> dict:
        """Discover every book across catalog pages and optionally sync them."""
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")

        config = source.config if source.plugin_name == "yuedu" else None
        plugin = self._source_plugin(source)

        stored_cookie = await load_source_cookie(source_id)
        if stored_cookie:
            plugin.set_cookie(safe_decrypt_cookie(stored_cookie))

        if not hasattr(plugin, "discover_books"):
            raise ValueError("当前书源不支持发现书籍，无法执行全站同步。")

        seen: set[str] = set()
        details: list[dict] = []
        books_found = 0
        books_synced = 0
        books_failed = 0
        books_filtered = 0
        chapters_created = 0
        chapters_skipped = 0
        chapters_failed = 0
        consecutive_failures = 0
        consecutive_transient_failures = 0
        max_consecutive_failures = max(
            1,
            int(getattr(settings, "SYNC_MAX_CONSECUTIVE_FAILURES", 10)),
        )
        pages_checked = 0
        start_page = max(1, int(start_page or 1))
        max_pages = max_pages or 0
        page_batch_size = max(0, int(page_batch_size or 0))
        pages_in_run = 0
        done = max_pages > 0 and start_page > max_pages

        def _result_so_far(*, done_state: bool, next_page: int) -> dict:
            """The run's counters, as a normal result (not an exception).

            Used when later pages stop answering after this run already synced
            books: the work is in the library, so the task must report it
            instead of failing and hiding it.
            """
            return {
                "source_id": source_id,
                "pages_checked": pages_checked,
                "books_found": books_found,
                "books_synced": books_synced,
                "books_failed": books_failed,
                "books_filtered": books_filtered,
                "chapters_created": chapters_created,
                "chapters_skipped": chapters_skipped,
                "chapters_failed": chapters_failed,
                "details": details,
                "next_page": next_page,
                "done": done_state,
            }

        async def _source_has_books() -> bool:
            try:
                return await self.db.scalar(
                    select(Book.id).where(Book.source_id == source_id).limit(1)
                ) is not None
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Could not check library for source {}: {}", source_id, exc,
                )
                await self.db.rollback()
                return False

        # Queue progress/checkpoints use the task session, including callbacks
        # from concurrent chapter producers. Give queued books their own
        # session even when the dispatcher assigns only one book at a time.
        queued = book_concurrency is not None
        if book_concurrency is None:
            book_concurrency = sync_book_concurrency()
        page = start_page
        while max_pages <= 0 or page <= max_pages:
            pages_in_run += 1
            try:
                if before_step is not None:
                    await before_step()
                page_books = await plugin.discover_books(url=url, page=page)
            except Exception as exc:
                if isinstance(exc, (SyncPaused, core_transient.UpstreamBackoff)) or (
                    backoff_cb is not None and is_transient_task_error(exc)
                ):
                    if backoff_cb is not None:
                        backoff_cb(
                            _result_so_far(done_state=False, next_page=page),
                            list(completed_page_urls or ()) if page == start_page else [],
                        )
                    raise
                # A catalog page that stops answering must not discard the work
                # already done in this run.  Icu (hq555) synced 244 books over
                # nine hours, then its JS explore rule returned nothing on the
                # next page (the site started demanding verification) and the
                # whole task was written off as "该书的发现规则是 Legado JS 脚本…
                # 当前环境无法执行" -- the JS had in fact just worked 244 times,
                # and the books never showed up anywhere except the library.
                message = describe_error(exc)
                await self.db.rollback()
                if books_synced or books_found:
                    logger.warning(
                        "Stopping discovery for {} at page {} after syncing {} books: {}",
                        source_id, page, books_synced, message,
                    )
                    return {
                        **_result_so_far(done_state=True, next_page=page),
                        "discovery_error": f"发现目录第 {page} 页中断：{message}",
                    }
                raise
            if not page_books:
                done = True
                break
            pages_checked = page

            new_books = []
            for sb in page_books:
                key = sb.url.split("#", 1)[0].rstrip("/")
                if not key or key in seen or not self._is_http_url(key):
                    continue
                seen.add(key)
                new_books.append(sb)

            if not new_books:
                done = True
                break

            # A rate-limited page is fetched again. Only revisit books without
            # a recorded outcome, even if the catalog order has changed.
            already_recorded = set(completed_page_urls or ()) if page == start_page else set()
            new_books = [sb for sb in new_books if sb.url not in already_recorded]
            page_detail_start = len(details)
            found_before_page = books_found

            continuous_books = bool(
                getattr(settings, "SYNC_BOOK_CONTINUOUS", False)
            )
            chapter_progress_lock = asyncio.Lock()

            async def _guarded_chapter_progress(info: dict) -> None:
                if chapter_progress_cb is None:
                    return
                async with chapter_progress_lock:
                    await chapter_progress_cb(info)

            async def _sync_one(sb):
                async with SessionLocal() as session:
                    return await SyncService(session, synopses=self._synopses).sync_book(
                        source_id,
                        sb.url,
                        progress_cb=_guarded_chapter_progress,
                        checkpoint_cb=checkpoint_cb,
                        discovery_tags=sb.tags,
                        exclude_tags=exclude_tags,
                        exclude_categories=exclude_categories,
                    )

            async def _record_outcome(sb_snapshot, outcome) -> None:
                nonlocal books_synced, books_failed, books_filtered
                nonlocal chapters_created, chapters_skipped, chapters_failed
                nonlocal consecutive_failures, consecutive_transient_failures
                sb_title, sb_author, sb_url = sb_snapshot
                if isinstance(outcome, SyncPaused):
                    raise outcome
                if isinstance(outcome, BaseException):
                    if isinstance(outcome, core_transient.UpstreamBackoff) or self._is_upstream_blocked(outcome):
                        raise outcome
                    if isinstance(outcome, EmptyTocError) or self._is_empty_book_error(outcome):
                        # The source's own TOC rule no longer matches the site
                        # (or the book genuinely has no chapters there).
                        # Retrying cannot fix a stale rule, and counting it as a
                        # failure aborted the whole task after
                        # ``SYNC_MAX_CONSECUTIVE_FAILURES`` books -- 御宅屋 has
                        # ~5% such books, so a full-site run died every time.
                        # Skip and keep going instead.
                        #
                        # ``no usable metadata/chapters`` is the same class: the
                        # book page answered but the rules extracted nothing
                        # usable (wn09's ``{{book.name}}`` title,
                        # a stale ``chapterList``).  It is deterministic -- a
                        # retry fetches the same page -- so it must not feed
                        # the consecutive-failure abort either.
                        await self.db.rollback()
                        books_filtered += 1
                        consecutive_failures = 0
                        consecutive_transient_failures = 0
                        message = describe_error(outcome)
                        logger.warning(
                            "Skipped book {} ({}): {}",
                            sb_title,
                            sb_url,
                            message,
                        )
                        details.append({
                            "title": sb_title,
                            "author": sb_author,
                            "url": sb_url,
                            "synced": False,
                            "filtered": True,
                            "filter_type": "目录",
                            "filter_value": message,
                        })
                        if progress_cb is not None:
                            await progress_cb(pages_checked, books_found, books_synced, books_failed)
                        return
                    await self.db.rollback()
                    books_failed += 1
                    if self._is_infrastructure_error(outcome):
                        # Our own stack failed (pool exhaustion, rolled-back
                        # session), not the site.  Record the book as failed so
                        # it is retried on the next run, but keep it out of both
                        # consecutive-failure counters: infrastructure noise must
                        # never abort a task, and especially never with a
                        # captcha/anti-crawl message.
                        logger.warning(
                            "Infrastructure failure syncing book {} ({}): {}",
                            sb_title,
                            sb_url,
                            describe_error(outcome),
                        )
                        details.append({
                            "title": sb_title,
                            "author": sb_author,
                            "url": sb_url,
                            "synced": False,
                            "error": describe_error(outcome),
                            "failed_chapters": [],
                        })
                    elif self._is_transient_book_fetch(outcome):
                        # Cloudflare 5xx / browser timeouts are a site or proxy
                        # hiccup, not an anti-crawl gate.  Aborting the task here
                        # used to hide the real cause behind a generic
                        # "consecutive failures" error; abort with a transient
                        # message instead so the task is retried later.
                        consecutive_failures = 0
                        consecutive_transient_failures += 1
                        if consecutive_transient_failures >= max_consecutive_failures:
                            books_failed -= 1
                            raise RuntimeError(
                                "上游/代理暂时不可用（网络错误，例如 Cloudflare "
                                f"520/5xx），已连续 {consecutive_transient_failures} "
                                "本同步失败；任务会在稍后自动重试，请检查代理节点或"
                                "稍后再试。"
                            )
                    else:
                        consecutive_transient_failures = 0
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            raise ValueError(
                                f"同步连续失败超过 {max_consecutive_failures} 本，已中止任务。"
                                f"最近一次失败：{describe_error(outcome)}"
                            )
                    if not self._is_infrastructure_error(outcome):
                        logger.warning(
                            "Failed to sync book {} ({}): {}",
                            sb_title,
                            sb_url,
                            describe_error(outcome),
                        )
                        details.append({
                            "title": sb_title,
                            "author": sb_author,
                            "url": sb_url,
                            "synced": False,
                            "error": describe_error(outcome),
                            "failed_chapters": [],
                        })
                else:
                    if outcome.get("filtered"):
                        books_filtered += 1
                        details.append({
                            "title": outcome.get("title") or sb_title,
                            "author": outcome.get("author") or sb_author,
                            "url": sb_url,
                            "synced": False,
                            "filtered": True,
                            "filter_type": outcome.get("filter_type"),
                            "filter_value": outcome.get("filter_value"),
                        })
                        if progress_cb is not None:
                            await progress_cb(pages_checked, books_found, books_synced, books_failed)
                        return
                    details.append({
                        "title": sb_title,
                        "author": sb_author,
                        "url": sb_url,
                        "synced": True,
                        "book_id": outcome.get("book_id"),
                        "created_chapters": outcome.get("created_chapters", 0),
                        "skipped_chapters": outcome.get("skipped_chapters", 0),
                        "failed_chapters": outcome.get("failed_chapters", []),
                    })
                    books_synced += 1
                    consecutive_failures = 0
                    consecutive_transient_failures = 0
                    chapters_created += outcome.get("created_chapters", 0)
                    chapters_skipped += outcome.get("skipped_chapters", 0)
                    chapters_failed += len(outcome.get("failed_chapters", []))

                if progress_cb is not None:
                    await progress_cb(pages_checked, books_found, books_synced, books_failed)

            active_tasks: dict[asyncio.Task, tuple] = {}

            def _snapshot_shelf_book(sb) -> tuple:
                """Freeze the fields ``_record_outcome`` needs off the ORM.

                ``sb`` is a ``RemoteShelfBook`` dataclass in production, so
                this is a no-op copy -- but discovery results flow through the
                same outcome path, and any future ORM-backed shelf object
                would otherwise be read *after* ``rollback()`` expired it
                (``MissingGreenlet`` masking the real book error).
                """
                return (
                    getattr(sb, "title", ""),
                    getattr(sb, "author", ""),
                    getattr(sb, "url", ""),
                )

            async def _record_finished(finished_tasks) -> None:
                backoff = None
                for finished in finished_tasks:
                    sb_snapshot = active_tasks.pop(finished)
                    if finished.cancelled():
                        continue
                    exc = finished.exception()
                    outcome = exc if exc is not None else finished.result()
                    if isinstance(outcome, core_transient.UpstreamBackoff):
                        if backoff is None or outcome.deadline > backoff.deadline:
                            backoff = outcome
                    else:
                        await _record_outcome(sb_snapshot, outcome)
                if backoff is not None:
                    raise backoff

            try:
                for sb in new_books:
                    if before_step is not None:
                        await before_step()
                    books_found += 1
                    if not sync:
                        details.append({
                            "title": sb.title,
                            "author": sb.author,
                            "url": sb.url,
                            "synced": False,
                        })
                        if progress_cb is not None:
                            await progress_cb(pages_checked, books_found, books_synced, books_failed)
                        continue
                    if book_concurrency <= 1 and not queued:
                        try:
                            result = await self.sync_book(
                                source_id,
                                sb.url,
                                progress_cb=chapter_progress_cb,
                                checkpoint_cb=checkpoint_cb,
                                discovery_tags=sb.tags,
                                exclude_tags=exclude_tags,
                                exclude_categories=exclude_categories,
                            )
                        except SyncPaused:
                            raise
                        except Exception as exc:
                            await _record_outcome(_snapshot_shelf_book(sb), exc)
                        else:
                            await _record_outcome(_snapshot_shelf_book(sb), result)
                        continue

                    task = asyncio.create_task(_sync_one(sb))
                    active_tasks[task] = _snapshot_shelf_book(sb)
                    if not continuous_books and len(active_tasks) >= book_concurrency:
                        finished_tasks, _ = await asyncio.wait(
                            list(active_tasks.keys()),
                            return_when=asyncio.ALL_COMPLETED,
                        )
                        await _record_finished(finished_tasks)
                        if before_step is not None:
                            await before_step()
                    elif continuous_books:
                        while len(active_tasks) >= book_concurrency:
                            finished_tasks, _ = await asyncio.wait(
                                list(active_tasks.keys()),
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            await _record_finished(finished_tasks)
                            if before_step is not None:
                                await before_step()

                while active_tasks:
                    finished_tasks, _ = await asyncio.wait(
                        list(active_tasks.keys()),
                        return_when=asyncio.ALL_COMPLETED,
                    )
                    await _record_finished(finished_tasks)
                    if before_step is not None:
                        await before_step()
            except BaseException as exc:
                for task in active_tasks:
                    task.cancel()
                if active_tasks:
                    await asyncio.gather(*active_tasks, return_exceptions=True)
                if backoff_cb is not None and (isinstance(exc, SyncPaused) or is_transient_task_error(exc)):
                    finished_urls = [item["url"] for item in details[page_detail_start:]]
                    snapshot = _result_so_far(done_state=False, next_page=page)
                    snapshot["books_found"] = found_before_page + len(finished_urls)
                    backoff_cb(snapshot, sorted(already_recorded.union(finished_urls)))
                raise
            if max_pages > 0 and page >= max_pages:
                done = True
                break
            if page_batch_size > 0 and pages_in_run >= page_batch_size:
                break
            page += 1

        if before_step is not None:
            try:
                await before_step()
            except SyncPaused:
                if backoff_cb is not None:
                    resume_page = pages_checked + 1 if pages_checked else start_page
                    backoff_cb(_result_so_far(done_state=False, next_page=resume_page),
                               list(completed_page_urls or ()) if not pages_checked else [])
                raise

        # Every page came back empty.  Why that is decides what the user sees:
        # a finished task, a transient hiccup worth retrying, or a broken rule.
        if pages_checked == 0 and books_found == 0 and done:
            if (max_pages > 0 and start_page > max_pages) or (start_page > 1 and not completed_page_urls):
                # The first page of a resumed batch can itself be the end.
                return _result_so_far(done_state=True, next_page=start_page)
            if not isinstance(config, dict):
                config = {}
            js_discovery = any(
                "<js>" in str(config.get(key) or "")
                for key in ("ruleExplore", "exploreUrl", "searchUrl")
            )
            known_book = await self.db.scalar(
                select(Book.id).where(Book.source_id == source_id).limit(1)
            )
            if known_book is not None or completed_page_urls:
                # The source already has books in the library, so an empty
                # catalog is a network/proxy/site hiccup rather than a rule
                # problem: a retried 要撸小说 task answered every category page
                # with HTTP 200 but nothing to parse right after the proxy
                # dropped connections.  Raise a message the crawl runner
                # classifies as transient so it retries instead of failing the
                # task with a misleading "no books" error.
                raise RuntimeError(
                    "书源目录本次未返回任何书籍（网络/代理波动、站点限流或临时验证都可能导致）。"
                    "已入库的书籍不受影响，任务稍后会自动重试；"
                    "若持续失败，请检查代理节点或站点验证状态。"
                    + _last_explore_diagnosis(plugin)
                )
            if js_discovery:
                # Only reachable with an *empty* library and not one page read:
                # a JS rule that works intermittently (Icu's explore script ran
                # 244 times before the site began asking for verification) hit
                # its site-side branch and returned nothing.
                raise ValueError(
                    "该书源的发现规则是 Legado JS 脚本，本次执行没有返回任何分类"
                    "（脚本本身能运行，通常是站点要求人机验证、限流或页面结构变了）。"
                    "请在浏览器里过验证后导入 Cookie，或稍后重试；"
                    "也可以直接换用该网站的其他书源。"
                    + _last_explore_diagnosis(plugin)
                )
            raise ValueError("书源未返回可同步的书籍，请检查书源规则、Cookie 或站点验证状态。")

        return {
            "source_id": source_id,
            "pages_checked": pages_checked,
            "books_found": books_found,
            "books_synced": books_synced,
            "books_failed": books_failed,
            "books_filtered": books_filtered,
            "chapters_created": chapters_created,
            "chapters_skipped": chapters_skipped,
            "chapters_failed": chapters_failed,
            "details": details,
            "next_page": pages_checked + 1 if pages_checked else start_page,
            "done": done,
        }
