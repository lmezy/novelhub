"""Book page and table-of-contents resolution for the YueDu plugin.

Split out of the ``YueduPlugin`` god class.  ``fetch_book`` orchestrates
metadata, the TOC and the chapter list; ``_find_toc_url`` guesses a
separate TOC page when the source declares no ``tocUrl``.  The guess is
kept conservative on purpose: a guessed 'directory' page is often a
site-wide index, and running the source's own ``ruleToc`` on the book
page (Legado's default semantics) is what actually recovers those books.
"""

from app.crawler.base import EmptyTocError
from app.crawler.base import RemoteBook
from app.crawler.base import RemoteChapter
from app.crawler.plugins.yuedu.common import logger
from app.crawler.plugins.yuedu.errors import is_transient_transport_error
from app.crawler.plugins.yuedu.selectors import TOC_LINK_PATH_RE
from app.crawler.plugins.yuedu.selectors import TOC_LINK_TEXTS
from bs4 import BeautifulSoup
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlparse
import asyncio
import random
import re


class BookMixin:
    """Methods extracted from ``YueduPlugin``."""

    def build_book_url(self, book_id: str) -> str:
        """Reconstruct a book detail URL from a stored source book id."""
        if book_id.startswith(("http://", "https://")):
            return book_id
        if book_id.startswith("/"):
            return urljoin(self.base_url, book_id)
        if "/" in book_id:
            return urljoin(self.base_url.rstrip("/") + "/", book_id)

        prefix = "/novel/"
        pattern = self.config.get("bookUrlPattern", "")
        if pattern:
            match = re.search(r"https?://[^/]+(/[^?#]*?)(?:\.html|\{[^}]+\}|[^/]+)$", pattern)
            if match:
                prefix = match.group(1).rsplit("/", 1)[0] + "/"
        return f"{self.base_url.rstrip('/')}{prefix}{book_id}"
    async def fetch_book(self, url: str) -> RemoteBook:
        """Fetch book info + chapter list from a book page URL."""
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")

        # A source may append a Legado ``,{...}`` option suffix to the book URL
        # (e.g. 要撸小说 appends `,{"webView":true}` to every book link).  That
        # suffix must NOT become the book's identity: it corrupts baseUrl, makes
        # source_book_id/url comparisons fail, and flows into the DB.  Keep it
        # only for the page fetch (so `_get` can honor webView/JS), and use the
        # clean URL for every identity/base operation below.
        fetch_url = url
        identity_url, url_options = self._split_options_suffix(url)
        if not identity_url:
            identity_url = url

        # Rules may refer to Legado's page-scoped ``baseUrl``.
        self.engine.set_page_url(identity_url)

        # Use webJs-enabled fetch if the source has webJs configured
        web_js = self.engine.get_web_js()
        if web_js:
            html = await self._get_with_web_js(fetch_url, web_js)
        else:
            html = await self._get(fetch_url)
        if self._looks_like_upstream_error(html):
            raise RuntimeError(
                "Upstream server returned a transient 5xx error page "
                f"(Cloudflare/520 etc.): {fetch_url}"
            )
        if self._looks_like_removed_page(html):
            raise RuntimeError(
                "该书在源站已被删除或禁用（站点提示：小说被禁用或已删除）: "
                f"{fetch_url}"
            )
        # ``ruleBookInfo`` often starts with ``{{book.name}}`` (绅士漫画 and
        # others): Legado resolves it against the book object the opening
        # request already carried.  NovelHub only knows the URL here, so clear
        # any book left over from a previous fetch on this engine instead of
        # letting ``{{book.name}}`` resolve to another book's title.
        self.engine.set_book({})
        info = self.engine.parse_book_info(html)

        # Run preUpdateJs before parsing TOC
        toc_data = {"bookUrl": identity_url, "baseUrl": self.base_url}
        self.engine.run_pre_update_js(toc_data)

        toc_url = str(info.get("tocUrl") or "").strip()
        # Remember whether the source *itself* declared where its catalogue is.
        # A URL guessed by ``_find_toc_url`` is only a hint, so the stricter
        # "the catalogue page is authoritative" rule below applies to the
        # declared one only.
        toc_url_from_rules = bool(toc_url)
        if not toc_url:
            toc_url = self._find_toc_url(html, identity_url)
        if toc_url and not toc_url.startswith(("http://", "https://")):
            toc_url = self._make_absolute(toc_url, identity_url)
        if not toc_url:
            toc_url = identity_url

        if toc_url.rstrip("/") == identity_url.rstrip("/"):
            toc_html = html
        else:
            toc_html = await self._get(toc_url)

        self.engine.set_page_url(toc_url)
        # Legado exposes the parsed book as the ``book`` JS variable; TOC
        # rules such as SiS's single-entry list reference ``book.name``.
        self.engine.set_book({
            "name": str(info.get("name") or "").strip(),
            "author": str(info.get("author") or "").strip(),
            "url": identity_url,
            "bookUrl": identity_url,
            "baseUrl": self.base_url,
        })
        toc = self._resolve_toc_entries(
            self.engine.parse_toc(toc_html),
            toc_url,
        )
        # Capture the image manifest produced by a TOC ``chapterList`` script
        # before another concurrent book can overwrite the JS runtime's global
        # variable store.
        self._chapter_image_manifest = self._read_chapter_image_manifest()
        # Whether the source's own ``ruleToc`` produced the list.  A rule that
        # matched is authoritative about what a chapter URL looks like; the
        # URL-shape heuristic below is only for the generic scanner, which
        # happily returns navigation links.
        toc_from_rules = bool(toc)
        android_toc_rule = self._uses_android_js_rule("ruleToc", "chapterList")
        # ``_find_toc_url`` only *guesses* a catalogue URL from a link on the
        # book page, and the guess can land on a site-wide index instead of this
        # book's TOC.  On 中文成人文学网 (blog.xbookcn.net) the post links to
        # ``/search/label/目录索引``, and the source's ruleToc -- written for the
        # post itself, which is also Legado's default when
        # ``ruleBookInfo.tocUrl`` is empty -- then returned one self-referential
        # entry ("book title -> the index URL"), which the chapter loop dropped
        # as a duplicate of the book title and left the book with 0 chapters.
        # A guessed page that yields nothing, or only entries pointing back at
        # itself, is useless: run the source's own rules on the book page
        # instead of letting the guess decide.
        guessed_toc_is_useless = (
            not toc
            or all(
                self._make_absolute(
                    str(entry.get("chapterUrl") or ""),
                    toc_url,
                ).rstrip("/") == toc_url.rstrip("/")
                for entry in toc
            )
        )
        if (
            guessed_toc_is_useless
            and not toc_url_from_rules
            and toc_url.rstrip("/") != identity_url.rstrip("/")
        ):
            self.engine.set_page_url(identity_url)
            retry_toc = self._resolve_toc_entries(
                self.engine.parse_toc(html),
                identity_url,
            )
            self.engine.set_page_url(identity_url if retry_toc else toc_url)
            if retry_toc:
                toc = retry_toc
                toc_from_rules = True
                toc_url = identity_url
                toc_html = html
                self._chapter_image_manifest = self._read_chapter_image_manifest()
            else:
                # Keep the generic fallback below reachable: a list of
                # self-referential entries is not a chapter list.
                toc = []
        if not toc and not android_toc_rule:
            # The configured ruleToc may be outdated. Fall back to the generic
            # chapter scanner on the real TOC page (book page or full list).
            generic_toc = self._parse_book_generic(toc_html, toc_url)
            toc = [
                {
                    "chapterName": chapter.title,
                    "chapterUrl": chapter.url,
                }
                for chapter in generic_toc["chapters"]
            ]

        # Follow nextTocUrl for paginated tables of contents
        max_toc_pages = 20
        seen_toc_urls = {toc_url}
        toc_pages_fetched = 1
        toc_semaphore = asyncio.Semaphore(self._thread_count())

        async def _fetch_toc_page(page_url: str) -> str:
            async with toc_semaphore:
                return await self._get(page_url)

        toc_pending = [
            next_toc_url
            for next_toc_url in self.engine.get_next_toc_urls(toc_html, toc_url)
            if next_toc_url not in seen_toc_urls
        ]
        seen_toc_urls.update(toc_pending)
        while toc_pending and toc_pages_fetched < max_toc_pages:
            batch = toc_pending
            toc_pending = []
            htmls = await asyncio.gather(
                *(_fetch_toc_page(page_url) for page_url in batch)
            )
            for page_url, page_html in zip(batch, htmls):
                if toc_pages_fetched >= max_toc_pages:
                    break
                toc_pages_fetched += 1
                toc.extend(
                    self._resolve_toc_entries(
                        self.engine.parse_toc(page_html),
                        page_url,
                    )
                )
                for next_toc_url in self.engine.get_next_toc_urls(
                    page_html,
                    page_url,
                ):
                    if next_toc_url not in seen_toc_urls:
                        seen_toc_urls.add(next_toc_url)
                        toc_pending.append(next_toc_url)

        generic = self._parse_book_generic(html, url)
        if not str(info.get("name") or "").strip():
            info["name"] = generic["title"]
        raw_rule_author = str(info.get("author") or "").strip()
        rule_author = self._clean_author(str(info.get("author") or "").strip())
        generic_author = self._clean_author(str(generic.get("author") or "").strip())
        labelled_author = self._clean_author(
            self._extract_labelled_author(BeautifulSoup(html, "lxml"))
        )
        if labelled_author and not self._looks_like_invalid_author(labelled_author):
            info["author"] = labelled_author
        elif not rule_author or self._looks_like_invalid_author(rule_author):
            info["author"] = generic_author or rule_author
        else:
            info["author"] = rule_author
        if not info.get("intro"):
            info["intro"] = generic["description"]
        if not info.get("status"):
            info["status"] = generic["status"]
        if not self._pick_cover_url(info.get("coverUrl"), url):
            info["coverUrl"] = self._pick_cover_url(generic.get("cover"), url)
        generic_tags = generic.get("tags") or []
        raw_kind = info.get("kind") or ""
        book_title = self._clean_book_title(str(info.get("name") or "").strip()) or "Unknown"
        author = self._clean_author(str(info.get("author") or "").strip()) or "Unknown"
        # The rule-defined ``ruleBookInfo.kind`` (the source's own category)
        # and the page's own keywords/tag list are both kept: sources such as
        # 爱丽丝书屋 declare only the broad category in ``kind`` while the page
        # carries the real tags.  What must never be scraped is the site's
        # navigation menu -- see ``_inside_navigation``.
        kind_tags = self._split_kind_text(raw_kind)
        tags = list(dict.fromkeys([
            *kind_tags,
            *generic_tags,
            *self._content_type_tags(
                book_title,
                info.get("intro"),
                generic.get("description"),
            ),
        ]))
        tags = self._clean_tags(
            tags,
            book_title,
            author,
            extra_noise=[raw_rule_author],
        )
        info["kind"] = ",".join(tags)
        info["name"] = book_title
        info["author"] = author

        chapters: list[RemoteChapter] = []
        self_chapter_title = ""
        chapter_num = 0
        seen_chapter_urls: set[str] = set()
        for ch in toc:
            # Skip volume headers
            is_volume = str(ch.get("isVolume", "")).strip().lower()
            if is_volume in ("true", "1", "yes"):
                continue

            title = ch.get("chapterName", f"Chapter {chapter_num + 1}")
            ch_url = ch.get("chapterUrl", "")
            if not ch_url:
                is_vip = str(ch.get("isVip", "")).strip().lower()
                is_pay = str(ch.get("isPay", "")).strip().lower()
                if is_vip in ("true", "1", "yes") or is_pay in ("true", "1", "yes"):
                    title = f"[VIP] {title}"
                continue
            # A ``chapterUrl`` rule may carry a Legado ``,{...}`` / ``{...}``
            # URL-option suffix (wn09's ``...@href##$##{"webView":true}``):
            # split the fetch identity from the request options the way
            # Legado's ``AnalyzeUrl`` does, so the identity stays a clean URL
            # while the webView flag survives for the chapter fetch.
            ch_url, chapter_options = self._split_options_suffix(str(ch_url))
            chapter_suffix = ""
            if chapter_options:
                web_view = bool(chapter_options.get("web_view"))
                if web_view:
                    chapter_suffix = ',{"webView":true}'
            if ch_url and not ch_url.startswith("http"):
                ch_url = self._make_absolute(ch_url, identity_url)
            if urlparse(ch_url).scheme not in ("http", "https"):
                continue
            title = str(title or "").strip() or f"Chapter {chapter_num + 1}"
            # Forum sources frequently model a post as a one-chapter book and
            # intentionally return the same URL from ruleToc. Keep it as a
            # fallback rather than discarding it as a book-detail URL.
            if ch_url.rstrip("/") == identity_url.rstrip("/"):
                self_chapter_title = self_chapter_title or title
                continue
            if not toc_from_rules and not self._is_chapter_url(
                ch_url,
                identity_url,
            ):
                continue
            if (
                title in ("目录", "简介", "上一章", "下一章", "返回目录", "首页", "开始阅读")
                or title == book_title
            ):
                continue
            if ch_url in seen_chapter_urls:
                continue
            seen_chapter_urls.add(ch_url)
            chapter_num += 1
            chapters.append(RemoteChapter(
                source_chapter_id=ch_url,
                title=title,
                url=ch_url + chapter_suffix,
                chapter_number=chapter_num,
            ))

        if not chapters and self_chapter_title:
            self_title = (
                self_chapter_title
                if self_chapter_title not in ("", "Chapter 1")
                else book_title
            )
            # A post can be the index of a whole work ("…1-23"): its own body
            # links to every other instalment, while ``ruleToc`` only ever saw
            # the thread title.  Keeping this page as the book's *only* chapter
            # left the rest of the work unreadable, so the linked parts become
            # chapters too -- the book page stays chapter 1 because it carries
            # the opening instalment.
            series_parts = self._series_index_parts(html, identity_url, book_title)
            if series_parts:
                logger.info(
                    "Series index expanded into %s chapters for %s",
                    len(series_parts) + 1,
                    identity_url,
                )
            chapters = [
                RemoteChapter(
                    source_chapter_id=url,
                    title=self_title,
                    url=url,
                    chapter_number=1,
                ),
                *[
                    RemoteChapter(
                        source_chapter_id=part["url"],
                        title=part["title"],
                        url=part["url"],
                        chapter_number=index,
                    )
                    for index, part in enumerate(series_parts, start=2)
                ],
            ]
        if not chapters and not android_toc_rule:
            if toc_url_from_rules and toc_url.rstrip("/") != identity_url.rstrip("/"):
                # The source declared a dedicated catalogue page and it came
                # back without a single chapter.  Scanning the book detail page
                # here is wrong: its links are recommendations ("相关推荐",
                # "作者其他作品"), which then turned into chapters that were
                # really other books and failed one by one with "Chapter
                # returned empty content".  Skip the book with a clear reason
                # instead of inventing a table of contents.
                raise EmptyTocError(
                    "书源目录规则已失效：书源声明的目录页没有解析出任何章节"
                    f"（{toc_url}）。该书在源站可能没有章节，或站点已把目录改成"
                    "动态加载，请更新书源规则或改用其它书源。"
                )
            chapters = generic["chapters"]
        if not chapters and self._has_forum_content(html):
            # Several Cool18-compatible sources use Android Jsoup in ruleToc.
            # The page itself is a complete post, so retain it as one chapter.
            chapters = [RemoteChapter(
                source_chapter_id=url,
                title=book_title,
                url=url,
                chapter_number=1,
            )]
        chapters = self._dedupe_chapters(chapters, identity_url)
        chapters = self._attach_next_urls(chapters)

        cover_url = self._pick_cover_url(info.get("coverUrl"), identity_url)
        description = info.get("intro", "")
        status = info.get("status", "")

        return RemoteBook(
            source_book_id=self._book_id_from_url(identity_url),
            title=book_title,
            author=author,
            description=description if description else None,
            status=status if status else None,
            chapters=chapters,
            tags=tags,
            cover_url=cover_url or None,
        )
    def _find_toc_url(self, html: str, book_url: str) -> str:
        """Find a full chapter-list URL from a book detail page.

        Many sites keep only the latest chapters on the book page and put the
        complete TOC on a separate page (for example `/other/chapters/id/1.html`
        or `/chapters/1.html`). The link text is usually "查看所有章节".
        """
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return ""

        candidates: list[str] = []
        for a_tag in soup.select("a[href]"):
            href = (a_tag.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "#")):
                continue
            text = a_tag.get_text(" ", strip=True).strip().lower()
            absolute = self._make_absolute(href, book_url or self.base_url)
            if not absolute.startswith(("http://", "https://")):
                continue
            if urlparse(absolute).path in ("", "/"):
                continue
            if absolute.rstrip("/") == self._make_absolute(
                book_url,
                self.base_url,
            ).rstrip("/"):
                continue
            if text and any(marker in text for marker in TOC_LINK_TEXTS):
                candidates.append(absolute)
                continue
            if TOC_LINK_PATH_RE.search(urlparse(absolute).path):
                candidates.append(absolute)

        # Prefer an explicit TOC path over a generic link text match.
        for candidate in candidates:
            if TOC_LINK_PATH_RE.search(urlparse(candidate).path):
                return candidate
        return candidates[0] if candidates else ""
    @staticmethod
    def _resolve_toc_entries(
        entries: list[dict[str, Any]],
        base_url: str,
    ) -> list[dict[str, Any]]:
        """Resolve TOC chapter URLs against the page they were parsed from."""
        for entry in entries:
            ch_url = str(entry.get("chapterUrl") or "").strip()
            if ch_url and not ch_url.startswith(("http://", "https://")):
                entry["chapterUrl"] = urljoin(base_url, ch_url)
        return entries
    @staticmethod
    def _attach_next_urls(chapters: list[RemoteChapter]) -> list[RemoteChapter]:
        return [
            RemoteChapter(
                source_chapter_id=chapter.source_chapter_id,
                title=chapter.title,
                url=chapter.url,
                chapter_number=chapter.chapter_number,
                next_url=(
                    chapters[index + 1].url
                    if index + 1 < len(chapters)
                    else None
                ),
            )
            for index, chapter in enumerate(chapters)
        ]
    async def fetch_cover(self, url: str) -> tuple[bytes, str] | None:
        """Fetch a cover image, applying coverDecodeJs when configured."""
        import asyncio

        if not url.startswith(("http://", "https://")):
            return None
        # A source may append a Legado ``,{...}`` URL-options suffix to the
        # cover URL too. Strip it before requesting so it is not sent as part
        # of the path (which turns into a 404 for otherwise valid images).
        clean_url, _ = self._split_options_suffix(url)
        url = clean_url or url
        if self._image_known_missing(url):
            return None
        await self._sleep_rate_limit()
        headers = self._build_headers({
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        })
        # Cover CDNs commonly reject a direct request without the book site's
        # Referer. A source-defined Referer wins over this default.
        headers.setdefault("Referer", self.base_url.rstrip("/") + "/")

        proxy_url = None
        try:
            from app.services.proxy_config import get_proxy_config
            cfg = get_proxy_config()
            if cfg.enabled:
                proxy_url = cfg.https_proxy or cfg.http_proxy
        except Exception:
            pass

        async def _request(proxy: str | None) -> tuple[bytes, str] | None:
            """Fetch over one transport; ``None`` means "gone for good"."""
            nonlocal headers
            last_error: Exception | None = None
            last_status: int | None = None
            for attempt in range(3):
                try:
                    client = await self._get_http_client(proxy)
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 403 and attempt == 0:
                        headers = self._with_403_fallback(headers)
                        await asyncio.sleep(1.0 + random.uniform(0.5, 1.0))
                        continue
                    if resp.status_code in (404, 410):
                        self._remember_missing_image(url)
                        logger.debug(
                            "Cover missing (HTTP %s): %s",
                            resp.status_code,
                            url,
                        )
                        return None
                    if resp.status_code in (429, 500, 502, 503, 504):
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.replace(".", "", 1).isdigit()
                            else 2 ** attempt
                        )
                        last_status = resp.status_code
                        await asyncio.sleep(wait + random.uniform(0.5, 1.5))
                        continue
                    resp.raise_for_status()
                    self._capture_cookie_jar(resp)
                    return resp.content, resp.headers.get("content-type", "")
                except Exception as exc:
                    if not is_transient_transport_error(exc):
                        raise
                    last_error = exc
                    await self._reset_http_client(proxy)
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
            if last_error is not None:
                raise last_error
            raise RuntimeError(
                f"Request failed after retries: {url}"
                + (f" (HTTP {last_status})" if last_status else "")
            )

        last_error: Exception | None = None
        for proxy in self._ordered_transports(proxy_url):
            try:
                result = await _request(proxy)
            except Exception as exc:
                if not is_transient_transport_error(exc):
                    last_error = exc
                    break
                last_error = exc
                if proxy is None:
                    break
                logger.warning(
                    "Configured proxy %s request failed for cover (%s%s); "
                    "retrying direct",
                    proxy_url,
                    type(exc).__name__,
                    f": {exc}" if str(exc) else "",
                )
                continue
            if result is None:
                return None
            data, content_type = result
            if not data or len(data) < 128:
                return None
            data = self._decode_inline_cover_rule(data)
            if self.engine:
                decoded = self.engine.decode_cover(data)
                if decoded:
                    data = decoded
            return data, content_type
        if last_error is not None:
            logger.warning(
                "Failed to fetch cover %s: %s",
                url,
                str(last_error).strip() or type(last_error).__name__,
            )
        return None
    def _decode_inline_cover_rule(self, data: bytes) -> bytes:
        """Support AES-CBC cover snippets exported by some YueDu sources."""
        rule = str((self.config.get("ruleBookInfo") or {}).get("coverUrl") or "")
        if "AES/CBC/PKCS5Padding" not in rule or "copyOfRange(raw, 0, 16)" not in rule:
            return data

        key_match = re.search(
            r"String\(\s*(['\"])(.*?)\1\s*\)\.getBytes",
            rule,
            re.DOTALL,
        )
        if not key_match or len(data) <= 16:
            return data
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding

            key = key_match.group(2).encode("utf-8")
            decryptor = Cipher(
                algorithms.AES(key), modes.CBC(data[:16])
            ).decryptor()
            padded = decryptor.update(data[16:]) + decryptor.finalize()
            unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
            return unpadder.update(padded) + unpadder.finalize()
        except Exception as exc:
            logger.debug("Could not decode inline cover rule: %s", exc)
            return data
