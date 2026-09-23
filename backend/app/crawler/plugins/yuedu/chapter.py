"""Chapter content extraction for the YueDu plugin.

Split out of the ``YueduPlugin`` god class.  Text chapters and image
(manga) chapters, including the gallery walk that follows a 'next image'
link when a source only exposes one index page at a time, and the
duplicate/noise filtering that decides whether what came back is really
the chapter.
"""

from app.crawler.base import RemoteChapter
from app.crawler.plugins.yuedu.common import logger
from app.crawler.plugins.yuedu.rule_engine import YueduRuleEngine
from bs4 import BeautifulSoup
from bs4 import Tag
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlparse
import asyncio
import json
import os
import re


class ChapterMixin:
    """Methods extracted from ``YueduPlugin``."""

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
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
        chapter_engine.set_chapter_context({
            "title": str(getattr(chapter, "title", "") or ""),
            "url": chapter.url,
            "tag": str(getattr(chapter, "tags", "") or ""),
        })
        generic_content = self._parse_chapter_content_generic(html, fetch_url)
        if self._uses_android_js_rule("ruleContent", "content"):
            content = generic_content
        else:
            try:
                content = chapter_engine.parse_content(html)
            except Exception:
                content = ""
            if (
                not content
                or self._looks_like_rule_diagnostic(content, html)
                or self._is_bare_image_url(content)
            ):
                content = generic_content
        parts = [content] if content else []

        # Follow nextContentUrl for multi-page chapters.  Manga/photo albums
        # paginate one image per page, so the walk has to run until the album
        # itself ends (no next link left), not until the source's
        # ``imgInfoList`` manifest is used up: that manifest only describes the
        # first album index page (12 entries for 绅士漫画), and stopping there
        # truncated every album to 12 images no matter how many it really has.
        gallery_limit = len(self._chapter_image_manifest)
        gallery_mode = gallery_limit > 1
        max_pages = self._gallery_page_limit() if gallery_mode else 20
        seen_content_urls = {chapter.url}
        content_semaphore = asyncio.Semaphore(self._thread_count())

        async def _fetch_content_page(page_url: str) -> str:
            async with content_semaphore:
                if web_js:
                    return await self._get_with_web_js(
                        page_url, web_js,
                        fallback_http=not chapter_web_view,
                    )
                return await self._get(page_url)

        pending_content_urls = [
            url
            for url in chapter_engine.get_next_content_urls(html, fetch_url)
            if (
                url not in seen_content_urls
                and url.startswith(("http://", "https://"))
            )
        ]
        if gallery_mode:
            gallery_next = self._gallery_next_url(html, fetch_url)
            if gallery_next and gallery_next not in seen_content_urls:
                pending_content_urls.append(gallery_next)
        seen_content_urls.update(pending_content_urls)
        pages_fetched = 0
        while pending_content_urls and pages_fetched < max_pages:
            eligible = [
                url
                for url in pending_content_urls
                if not (
                    getattr(chapter, "next_url", None)
                    and url == chapter.next_url
                )
            ]
            batch = eligible[: max_pages - pages_fetched]
            pending_content_urls = eligible[max_pages - pages_fetched:]
            if not batch:
                break
            htmls = await asyncio.gather(
                *(_fetch_content_page(page_url) for page_url in batch)
            )
            for next_url, next_html in zip(batch, htmls):
                if pages_fetched >= max_pages:
                    break
                chapter_engine.set_page_url(next_url)
                if self._uses_android_js_rule("ruleContent", "content"):
                    next_part = self._parse_chapter_content_generic(next_html, next_url)
                else:
                    try:
                        next_part = chapter_engine.parse_content(next_html)
                    except Exception:
                        next_part = ""
                    if not next_part or self._looks_like_rule_diagnostic(next_part, next_html):
                        next_part = self._parse_chapter_content_generic(next_html, next_url)
                if next_part and next_part != next_html:
                    parts.append(next_part)
                pages_fetched += 1
                next_candidates = [
                    url
                    for url in chapter_engine.get_next_content_urls(next_html, next_url)
                    if (
                        url not in seen_content_urls
                        and url.startswith(("http://", "https://"))
                    )
                ]
                if gallery_mode:
                    gallery_next = self._gallery_next_url(next_html, next_url)
                    if gallery_next and gallery_next not in seen_content_urls:
                        next_candidates.append(gallery_next)
                pending_content_urls.extend(next_candidates)
            seen_content_urls.update(pending_content_urls)

        if gallery_mode and pages_fetched >= max_pages:
            logger.warning(
                "Gallery walk stopped at the page cap (%s pages) for %s; "
                "raise YUEDU_GALLERY_MAX_PAGES if this album is really larger",
                max_pages,
                chapter.url,
            )

        content = self._dedupe_content_images("\n".join(parts))

        if content and ("<" in content or ">" in content):
            try:
                content = self._content_text_preserving_images(content)
            except Exception:
                pass

        if not content:
            content = self._parse_chapter_content_generic(html, fetch_url)
        content = content.strip()
        if self._content_is_blocked(content):
            raise RuntimeError(
                "Chapter content looks like an anti-bot/captcha page "
                f"(site asked for verification): {chapter.url}"
            )
        replace_rules = (self.config.get("ruleContent") or {}).get("replaceRegex", [])
        if replace_rules:
            content = chapter_engine._apply_replace_regex(content, replace_rules).strip()
        if not content:
            raise RuntimeError(
                f"Chapter returned empty content: {chapter.url}"
            )
        return content
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
        """Upper bound for one image-album walk (``YUEDU_GALLERY_MAX_PAGES``).

        Album pages hand out exactly one image per request, so the walk stops
        when the source stops offering a next page; this cap only exists to
        keep a broken/looping "next" link from fetching forever.
        """
        try:
            value = int(os.getenv("YUEDU_GALLERY_MAX_PAGES", "512") or 512)
        except (TypeError, ValueError):
            value = 512
        return max(20, min(value, 5000))
    @staticmethod
    def _gallery_next_url(html: str, current_url: str) -> str:
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
