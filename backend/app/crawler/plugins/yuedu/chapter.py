"""Chapter content extraction for the YueDu plugin.

Split out of the ``YueduPlugin`` god class.  Text chapters and image
(manga) chapters, including the gallery walk that follows a 'next image'
link when a source only exposes one index page at a time, and the
duplicate/noise filtering that decides whether what came back is really
the chapter.
"""

from app.crawler.base import RemoteChapter
from app.services.book_kind import is_comic_content, is_comic_source
from app.crawler.plugins.yuedu.common import logger
from app.crawler.plugins.yuedu.rule_engine import YueduRuleEngine
from app.crawler.plugins.yuedu.selectors import CHAPTER_CHROME_PHRASES
from bs4 import BeautifulSoup
from bs4 import Tag
from collections import deque
from collections.abc import Awaitable, Callable
from itertools import groupby
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlparse
import asyncio
import json
import os
import re


IMAGE_LINE = re.compile(r"!\[[^\]]*\]\((\S+)\)")


class ChapterMixin:
    """Methods extracted from ``YueduPlugin``."""

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        return await self._fetch_chapter_content(chapter, progress_cb=None)

    async def fetch_chapter_content_with_progress(
        self,
        chapter: RemoteChapter,
        progress_cb: Callable[[int], Awaitable[None]],
    ) -> str:
        return await self._fetch_chapter_content(chapter, progress_cb=progress_cb)

    async def _fetch_chapter_content(
        self,
        chapter: RemoteChapter,
        *,
        progress_cb: Callable[[int], Awaitable[None]] | None,
    ) -> str:
        """Fetch and return the full text of a single chapter.

        Supports multi-page chapters via nextContentUrl rule.
        Uses webJs for JS-rendered pages when configured.
        """
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")

        # A plugin can fetch several chapters concurrently. The shared engine
        # stores page-scoped variables (baseUrl/bookUrl), so sharing it here
        # lets one in-flight request overwrite another chapter's context.
        # Use a private engine for the complete parse of this chapter.
        chapter_engine = YueduRuleEngine(self.config)
        # A ``chapter.url`` may carry a Legado ``,{...}`` URL-option suffix
        # (wn09 appends ``,{"webView":true}`` to every album URL): strip it
        # for identity/page-context purposes, exactly like ``fetch_book``
        # does, and let ``_get`` dispatch on the webView flag.
        fetch_url = chapter.url
        _, chapter_options = self._split_options_suffix(str(chapter.url or ""))
        chapter_web_view = bool((chapter_options or {}).get("web_view"))
        if chapter_web_view:
            fetch_url = str(fetch_url).split(",{", 1)[0].strip()
        chapter_engine.set_page_url(fetch_url)
        web_js = chapter_engine.get_web_js()
        if web_js:
            html = await self._get_with_web_js(fetch_url, web_js,
                                               fallback_http=not chapter_web_view)
        else:
            html = await self._get(fetch_url)
        if self._looks_like_removed_page(html):
            raise RuntimeError(
                "章节在源站已被删除或禁用（站点提示：小说被禁用或已删除）: "
                + chapter.url
            )
        if self._looks_like_upstream_error(html):
            raise RuntimeError(
                "Upstream server returned a transient 5xx error page "
                f"(Cloudflare/520 etc.): {chapter.url}"
            )
        page_fetches_completed = 1
        if progress_cb is not None:
            await progress_cb(page_fetches_completed)
        chapter_engine.set_chapter_context({
            "title": str(getattr(chapter, "title", "") or ""),
            "url": chapter.url,
            "tag": str(getattr(chapter, "tags", "") or ""),
        })
        content = self._extract_chapter_page(chapter_engine, html, fetch_url)
        parts = [content] if content else []

        # Follow nextContentUrl for multi-page chapters.  Manga/photo albums
        # paginate one image per page, so the walk has to run until the album
        # itself ends (no next link left), not until the source's
        # ``imgInfoList`` manifest is used up: that manifest only describes the
        # first album index page (12 entries for 绅士漫画), and stopping there
        # truncated every album to 12 images no matter how many it really has.
        has_manifest = bool(self._chapter_image_manifest)
        image_source = is_comic_source(self.config)
        gallery_mode = has_manifest or image_source or is_comic_content(content)
        # A declared pagination rule is authoritative for ordinary image
        # sources. Manifest-based albums also expose a separate viewer link.
        use_gallery_link = has_manifest or (
            gallery_mode and not (self.config.get("ruleContent") or {}).get("nextContentUrl")
        )
        max_pages = self._gallery_page_limit() if gallery_mode else 20
        parallelism = self._thread_count()
        page_progress_lock = asyncio.Lock()

        async def _report_content_page_fetch() -> None:
            nonlocal page_fetches_completed
            if progress_cb is None:
                return
            async with page_progress_lock:
                page_fetches_completed += 1
                await progress_cb(page_fetches_completed)

        async def _fetch_content_page(page_url: str) -> str:
            if web_js:
                page_html = await self._get_with_web_js(
                    page_url, web_js,
                    fallback_http=not chapter_web_view,
                )
            else:
                page_html = await self._get(page_url)
            await _report_content_page_fetch()
            return page_html

        def page_key(url: str) -> str:
            bare, options = self._split_options_suffix(url)
            # Fragments and a webView rendering flag can differ between a
            # chapter's TOC entry and its link. POST bodies identify pages.
            key = bare.split("#", 1)[0]
            if options:
                render_only = (
                    options["web_view"] and options["method"] == "GET" and not options["body"]
                    and not options["headers"] and not options["charset"] and not options["web_js"]
                )
                if not render_only:
                    key += url[len(bare):]
            return key

        seen = {page_key(chapter.url)}
        for neighbour in (getattr(chapter, "previous_url", None), getattr(chapter, "next_url", None)):
            if neighbour:
                seen.add(page_key(neighbour))
        pending: deque[str] = deque()

        def enqueue(page_html: str, page_url: str) -> None:
            links = chapter_engine.get_next_content_urls(page_html, page_url)
            if use_gallery_link:
                gallery_next = self._gallery_next_url(
                    page_html, page_url, require_page_label=not (has_manifest or image_source),
                )
                if gallery_next:
                    links.append(gallery_next)
            for link in links:
                if link.startswith(("http://", "https://")):
                    key = page_key(link)
                    if key not in seen:
                        seen.add(key)
                        pending.append(link)

        enqueue(html, fetch_url)
        pages_fetched = 0
        seen_gallery_parts = {content} if gallery_mode else set()
        repeats = 0
        while pending and (not max_pages or pages_fetched < max_pages):
            batch_size = min(parallelism, len(pending))
            if max_pages:
                batch_size = min(batch_size, max_pages - pages_fetched)
            batch = [pending.popleft() for _ in range(batch_size)]
            fetches = [asyncio.create_task(_fetch_content_page(url)) for url in batch]
            try:
                htmls = await asyncio.gather(*fetches)
            except BaseException:
                for fetch in fetches:
                    fetch.cancel()
                await asyncio.gather(*fetches, return_exceptions=True)
                raise
            for next_url, next_html in zip(batch, htmls):
                if self._looks_like_upstream_error(next_html):
                    raise RuntimeError(f"Upstream server returned a transient 5xx error page: {next_url}")
                chapter_engine.set_page_url(next_url)
                next_part = self._extract_chapter_page(chapter_engine, next_html, next_url)
                if next_part and next_part != next_html:
                    parts.append(next_part)
                pages_fetched += 1
                enqueue(next_html, next_url)
                # A broken next rule can mint a fresh URL forever for the
                # same viewer page. Report the loop instead of saving a
                # partial album as a successful chapter.
                if gallery_mode and len(batch) == 1 and len(pending) == 1 and next_part in seen_gallery_parts:
                    repeats += 1
                    if repeats >= 4:
                        raise ValueError("图集分页连续返回相同内容，请检查书源的下一页规则")
                else:
                    repeats = 0
                if gallery_mode:
                    seen_gallery_parts.add(next_part)

        if gallery_mode and pending:
            logger.warning("Gallery reached YUEDU_GALLERY_MAX_PAGES=%s at %s", max_pages, chapter.url)

        content = self._dedupe_content_images("\n".join(parts))

        if content and ("<" in content or ">" in content):
            try:
                content = self._content_text_preserving_images(content)
            except Exception:
                pass

        if not content:
            content = self._parse_chapter_content_generic(html, fetch_url)
        content = content.strip()
        previous_url = getattr(chapter, "previous_url", None)
        if previous_url and pages_fetched == 0 and not has_manifest:
            content = await self._trim_cumulative_gallery(
                content, fetch_url, previous_url, _fetch_content_page,
            )
        if self._content_is_blocked(content):
            raise RuntimeError(
                "Chapter content looks like an anti-bot/captcha page "
                f"(site asked for verification): {chapter.url}"
            )
        if self._content_is_chapter_chrome(content):
            # The page answered without its body and left the reader's own
            # navigation behind ("投推荐票 上一章 → 章节目录 → 下一章 加入书签" on
            # 御宅屋).  Failing is the honest outcome: the sync records a
            # chapter-level failure and retries later, instead of storing a
            # navigation line -- or an ad image next to it -- as the chapter.
            raise RuntimeError(
                "Chapter page returned only navigation/ads, not the chapter "
                f"text: {chapter.url}"
            )
        replace_rules = (self.config.get("ruleContent") or {}).get("replaceRegex", [])
        if replace_rules:
            content = chapter_engine._apply_replace_regex(content, replace_rules).strip()
        if not content:
            raise RuntimeError(
                f"Chapter returned empty content: {chapter.url}"
            )
        return content

    def _extract_chapter_page(self, engine: YueduRuleEngine, html: str, url: str) -> str:
        if not self._uses_android_js_rule("ruleContent", "content"):
            try:
                content = engine.parse_content(html)
            except Exception:
                content = ""
            if content and not (
                self._looks_like_rule_diagnostic(content, html)
                or self._is_bare_image_url(content)
            ):
                return content
        return self._parse_chapter_content_generic(html, url)

    @staticmethod
    def _image_only_lines(content: str, url: str) -> list[tuple[str, str]]:
        images = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            match = IMAGE_LINE.fullmatch(line)
            if match is None:
                return []
            absolute = urljoin(url, match[1]).split("#", 1)[0]
            images.append((line, absolute))
        return images

    async def _trim_cumulative_gallery(
        self,
        content: str,
        url: str,
        previous_url: str,
        fetch_page: Callable[[str], Awaitable[str]],
    ) -> str:
        """Drop earlier episodes when the source returns a cumulative gallery."""
        images = self._image_only_lines(content, url)
        # Ordinary chapters usually use one image directory. Require ordered,
        # numbered sibling directories before making another source request.
        directories: list[tuple[str, str, str]] = []
        for _, image_url in images:
            parsed = urlparse(image_url)
            parts = parsed.path.rsplit("/", 2)
            if (
                parsed.scheme not in ("http", "https") or not parsed.netloc
                or len(parts) != 3 or not re.fullmatch(r"[0-9]{1,9}", parts[1])
            ):
                return content
            directories.append((f"{parsed.scheme}://{parsed.netloc}", parts[0], parts[1]))
        groups = [directory for directory, _ in groupby(directories)]
        if len(groups) < 2 or len({group[:2] for group in groups}) != 1:
            return content
        numbers = [int(group[2]) for group in groups]
        if any(first >= second for first, second in zip(numbers, numbers[1:])):
            return content

        previous_page_url, _ = self._split_options_suffix(previous_url)
        if not previous_page_url or previous_page_url == url:
            return content
        try:
            previous_html = await fetch_page(previous_url)
            engine = YueduRuleEngine(self.config)
            engine.set_page_url(previous_page_url)
            previous_content = self._extract_chapter_page(engine, previous_html, previous_page_url)
            if "<" in previous_content or ">" in previous_content:
                previous_content = self._content_text_preserving_images(previous_content)
            previous_images = self._image_only_lines(previous_content, previous_page_url)
        except Exception as exc:
            logger.debug("Could not compare overlapping gallery chapters: %s", type(exc).__name__)
            return content

        previous_count = len(previous_images)
        if not 0 < previous_count < len(images):
            return content
        if directories[previous_count - 1] == directories[previous_count]:
            return content
        if any(old[1] != current[1] for old, current in zip(previous_images, images)):
            return content
        return "\n".join(line for line, _ in images[previous_count:])

    @staticmethod
    def _content_is_chapter_chrome(content: str) -> bool:
        """Whether extracted "content" is only the reader's own navigation.

        Deliberately narrow: a short block (no room for a real paragraph) that
        carries at least two distinct chrome phrases.  A real chapter cannot say
        "上一章" *and* "加入书签" *and* "目录" inside 120 characters while having
        no prose at all.
        """
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", str(content or ""))
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) > 120:
            return False
        hits = {phrase for phrase in CHAPTER_CHROME_PHRASES if phrase in text}
        return len(hits) >= 2
    def _read_chapter_image_manifest(self) -> list[dict[str, str]]:
        """Read ``imgInfoList`` captured by a manga source's TOC script.

        The variable name comes from the source's own ``chapterList`` rule.
        Reading it only for sources that explicitly use a manifest avoids
        treating a stale global value from an unrelated source as real.
        """
        if self.engine is None:
            return []
        toc_rules = json.dumps(
            self.config.get("ruleToc") or {},
            ensure_ascii=False,
        )
        if "imgInfoList" not in toc_rules:
            return []
        value = self.engine._try_eval_js(
            "String(java.get('imgInfoList'))",
            "",
        )
        if isinstance(value, (dict, list)):
            payload = value
        elif isinstance(value, str) and value.strip():
            try:
                payload = json.loads(value)
            except (TypeError, ValueError):
                return []
        else:
            return []
        if not isinstance(payload, list):
            return []

        manifest: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("imgName") or "").strip()
            extension = str(item.get("imgExtension") or "").strip().lower()
            if not name or not re.fullmatch(r"[a-z0-9]{1,8}", extension):
                continue
            manifest.append({"imgName": name, "imgExtension": extension})
        return manifest
    @staticmethod
    def _gallery_page_limit() -> int:
        """Optional maximum number of follow-up pages, zero reads to the end."""
        try:
            return max(0, int(os.getenv("YUEDU_GALLERY_MAX_PAGES", "0") or 0))
        except ValueError:
            return 0

    @staticmethod
    def _gallery_next_url(html: str, current_url: str, *, require_page_label: bool = False) -> str:
        """Find the next page of an image gallery.

        Manga/photo sites usually expose this as ``a.btnnext`` / ``rel=next``
        or visible text such as ``下一张``.  Keep the same host and strip the
        fragment so ``#pic_block`` does not look like a new page.
        """
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return ""

        current = current_url.split("#", 1)[0].rstrip("/")
        current_host = urlparse(current_url).netloc.lower()
        candidates: list[Tag] = list(
            soup.select('a.btnnext[href], a[rel="next"][href], link[rel="next"][href]')
        )
        for anchor in soup.select("a[href]"):
            text = anchor.get_text(" ", strip=True).lower()
            descriptor = " ".join([
                str(anchor.get("id") or ""),
                " ".join(str(c) for c in (anchor.get("class") or [])),
                text,
            ]).lower()
            if "prev" in descriptor or "上一" in descriptor:
                continue
            if "next" in descriptor or text in (
                "下一张", "下一張", "下一页", "下一頁", "下页", "下頁",
            ):
                candidates.append(anchor)

        seen: set[int] = set()
        for anchor in candidates:
            marker = id(anchor)
            if marker in seen:
                continue
            seen.add(marker)
            label = " ".join((anchor.get_text(" ", strip=True), str(anchor.get("title") or ""),
                              str(anchor.get("aria-label") or "")))
            if require_page_label and not re.search(r"下一?[張张页頁]|next\s+(?:image|page)", label, re.I):
                continue
            if re.search(r"下[一個个]?[章回話话卷集]|next\s+(?:chapter|episode|volume)", label, re.I):
                continue
            href = str(anchor.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "#")):
                continue
            target = urljoin(current_url, href).split("#", 1)[0]
            if not target.startswith(("http://", "https://")):
                continue
            if target.rstrip("/") == current:
                continue
            if current_host and urlparse(target).netloc.lower() != current_host:
                continue
            return target
        return ""
    @staticmethod
    def _count_content_images(content: str) -> int:
        return len(re.findall(
            r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*\bsrc=",
            str(content or ""),
            re.IGNORECASE,
        ))
    @classmethod
    def _is_bare_image_url(cls, content: str) -> bool:
        """Whether content is one plain image URL rather than readable text."""
        value = str(content or "").strip()
        if not value or "\n" in value or not cls._IMAGE_FILE_RE.search(value):
            return False
        return bool(re.fullmatch(r"(?:https?:)?//\S+", value))
    @staticmethod
    def _has_forum_content(html: str) -> bool:
        """Whether a forum post contains a readable post body."""
        try:
            return BeautifulSoup(html, "lxml").select_one(
                "#content-section, .content-section"
            ) is not None
        except Exception:
            return False
    def _chapter_images(self, soup: BeautifulSoup, base_url: str = "") -> list[str]:
        """Markdown image references for a chapter page that is only images."""
        return self._image_refs_from_tags(soup.find_all("img"), base_url)
    def _gallery_primary_images(
        self,
        soup: BeautifulSoup,
        base_url: str = "",
    ) -> list[str]:
        """Image references from common manga/photo viewer containers."""
        selectors = (
            "#imgarea img",
            ".gallery img",
            "#photo_body img.photo",
            ".photo_body img",
            ".manga-images img",
            ".comic-images img",
            "img#picarea",
            "img.photo",
        )
        for selector in selectors:
            refs = self._image_refs_from_tags(soup.select(selector), base_url)
            if refs:
                return refs
        return []
    def _image_refs_from_tags(
        self,
        tags: Any,
        base_url: str = "",
    ) -> list[str]:
        """Convert image tags into deduplicated markdown references."""
        refs: list[str] = []
        seen: set[str] = set()
        for img in tags:
            src = (
                img.get("data-src")
                or img.get("data-original")
                or img.get("data-lazy-src")
                or img.get("src")
                or ""
            )
            src = str(src).strip()
            if not src or src.startswith("data:"):
                continue
            descriptor = " ".join([
                src,
                str(img.get("id") or ""),
                " ".join(str(c) for c in (img.get("class") or [])),
            ])
            if self._NOISE_IMAGE_RE.search(descriptor):
                continue
            if not self._IMAGE_FILE_RE.search(src):
                continue
            if self._inside_navigation(img):
                continue
            url = urljoin(base_url, src) if base_url else src
            if url.startswith("//"):
                url = "https:" + url
            if url in seen:
                continue
            seen.add(url)
            alt = str(img.get("alt") or "").strip()
            refs.append(f"![{alt}]({url})")
        return refs
