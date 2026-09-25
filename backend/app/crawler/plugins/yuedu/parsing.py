"""Generic HTML parsing and text hygiene for the YueDu plugin.

Split out of the ``YueduPlugin`` god class.  These are the fallbacks
used when a book source declares no rule (or its rule found nothing):
pull a title/author/cover/tags/chapters out of an arbitrary page, then
clean the result.  The cleaners are the accumulated answer to real
site chrome leaking into metadata -- nav menus becoming 'tags', a
footer email-protection link becoming a 'chapter', a 345-byte Japanese
title overflowing a filesystem component limit.
"""

from app.crawler.base import RemoteChapter
from app.crawler.plugins.yuedu.rule_engine import YueduRuleEngine
from app.crawler.plugins.yuedu.selectors import GENERIC_BOOK_AUTHOR_SELECTORS
from app.crawler.plugins.yuedu.selectors import GENERIC_BOOK_COVER_SELECTORS
from app.crawler.plugins.yuedu.selectors import GENERIC_BOOK_DESC_SELECTORS
from app.crawler.plugins.yuedu.selectors import GENERIC_BOOK_TITLE_SELECTORS
from app.crawler.plugins.yuedu.selectors import GENERIC_CHAPTER_SELECTORS
from app.crawler.plugins.yuedu.selectors import SERIES_INDEX_MAX_PARTS
from app.crawler.plugins.yuedu.selectors import SERIES_INDEX_PART_TAIL_MAX
from app.crawler.plugins.yuedu.selectors import SERIES_INDEX_PREFIX_MIN
from app.crawler.plugins.yuedu.selectors import SERIES_INDEX_SKIP_TEXTS
from bs4 import BeautifulSoup
from bs4 import Tag
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlparse
import json
import re


class ParsingMixin:
    """Methods extracted from ``YueduPlugin``."""

    def _parse_book_generic(
        self,
        html: str,
        url: str,
    ) -> dict[str, Any]:
        """Parse a detail page using common novel-site patterns.

        This is a fallback for YueDu sources whose configured rules no longer
        match the live page. It extracts title/author/description and any
        chapter links so a source can still sync without a rule rewrite.
        """
        soup = BeautifulSoup(html, "lxml")
        title = ""
        for selector in GENERIC_BOOK_TITLE_SELECTORS:
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if value:
                title = str(value).strip()
                break

        if not title:
            title = self._extract_labelled_title(soup)

        if not title:
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(" ", strip=True)
                title = re.split(r"\s+[-_|]\s+", title, maxsplit=1)[0].strip()
        title = self._clean_book_title(title)

        # Prefer an explicitly labelled author from the page metadata/body.
        # Forum sources often use meta[name=author] for the post submitter,
        # while the novel's real author is embedded in the subject/description.
        author = self._extract_author_from_text(soup)
        for selector in GENERIC_BOOK_AUTHOR_SELECTORS:
            if author:
                break
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if value:
                author = str(value).strip()
                break
        author = self._clean_author(author)

        description = ""
        for selector in GENERIC_BOOK_DESC_SELECTORS:
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if value:
                description = str(value).strip()
                break

        cover = ""
        for selector in GENERIC_BOOK_COVER_SELECTORS:
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get("src") or el.get("data-src")
            if value:
                cover = str(value).strip()
                break
        if not cover:
            for img in soup.select("img[src]"):
                src = str(img.get("src") or "").strip()
                if not src or src.startswith("data:"):
                    continue
                abs_src = self._make_absolute(src, url)
                path = urlparse(abs_src).path.lower()
                if any(key in path for key in ("cover", "book", "novel")) and "favicon" not in path:
                    cover = src
                    break
        if cover:
            cover = self._make_absolute(cover, url)

        status = ""
        page_text = soup.get_text(" ", strip=True)
        for marker in ("已完结", "完结", "连载中", "连载"):
            if marker in page_text:
                status = "completed" if marker in ("已完结", "完结") else "ongoing"
                break

        tags: list[str] = []
        seen_tags: set[str] = set()
        page_site_markers = list(self._site_markers())
        site_name_meta = soup.select_one("meta[property='og:site_name']")
        if site_name_meta and site_name_meta.get("content"):
            page_site_markers.append(str(site_name_meta.get("content")).strip())
        document_title = soup.find("title")
        if document_title:
            title_text = document_title.get_text(" ", strip=True)
            if " - " in title_text:
                page_site_markers.extend(
                    part.strip()
                    for part in re.split(r"\s+", title_text.rsplit(" - ", 1)[-1])
                    if len(part.strip()) >= 2
                )
        normalized_site_markers = {
            re.sub(r"[^\w\u3400-\u9fff]+", "", marker).lower()
            for marker in page_site_markers
            if marker
        }

        def _add_tag(value: str, element: Any = None) -> None:
            value = value.strip().strip("#").strip()
            if not value or len(value) > 20 or value.lower() in (
                "tags", "tag", "标签", "分类", "类别", "类型", "最新章节",
            ):
                return
            # A category link inside the site chrome (nav/header/footer/menu)
            # is navigation, not a tag for this book.  要撸小说 repeats its whole
            # 书库/完本/玄幻/都市/… menu on every book page, which used to turn
            # every book's tags into the site's navigation list.
            if element is not None and self._inside_navigation(element):
                return
            normalized = re.sub(r"[^\w\u3400-\u9fff]+", "", value).lower()
            if normalized in normalized_site_markers:
                return
            if value not in seen_tags:
                seen_tags.add(value)
                tags.append(value)

        meta_keywords = soup.find(
            "meta",
            attrs={"name": re.compile(r"^keywords$", re.I)},
        )
        if meta_keywords and meta_keywords.get("content"):
            content = str(meta_keywords["content"]).replace("，", ",")
            for part in content.split(","):
                _add_tag(part)

        for selector in (
            ".tags a",
            ".tag a",
            "[class*='tag'] a",
            "[class*='kind'] a",
            "[class*='category'] a",
        ):
            for link in soup.select(selector):
                _add_tag(link.get_text(" ", strip=True), link)

        # Legado forum sources commonly expose tags as plain text rather than
        # links, for example: `标签：#奇幻 #后宫 #异世界`.
        for match in re.finditer(
            r"(?:标签|標籤|关键词|關鍵詞)\s*[:：]\s*([^\n\r]{1,240})",
            soup.get_text("\n", strip=True),
            re.IGNORECASE,
        ):
            raw_tags = match.group(1)
            for part in re.split(r"[#＃,，、;；|\s]+", raw_tags):
                _add_tag(part)

        for link in soup.select("a[href]"):
            href = (link.get("href") or "").strip()
            if not href or href.startswith("javascript:"):
                continue
            abs_href = self._make_absolute(href, url)
            path = urlparse(abs_href).path.lower()
            if any(
                segment in path
                for segment in (
                    "/tag/", "/tags/", "/booktag/", "/booktags/",
                    "/category/", "/categories/", "/fenlei/", "/sort/",
                )
            ):
                _add_tag(link.get_text(" ", strip=True), link)

        chapter_links: list[Tag] = []
        for selector in GENERIC_CHAPTER_SELECTORS:
            links = soup.select(selector)
            if len(links) >= 2:
                chapter_links = links
                break

        if not chapter_links:
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").strip()
                if not href or href == "#" or href.startswith("javascript:"):
                    continue
                abs_url = self._make_absolute(href, url)
                if self._is_chapter_url(abs_url, url):
                    chapter_links.append(a)

        chapters: list[RemoteChapter] = []
        seen_urls: set[str] = set()
        chapter_number = 0
        skip_titles = {"目录", "简介", "上一章", "下一章", "返回目录", "首页"}
        for a in chapter_links:
            text = a.get_text(" ", strip=True)
            if not text or text in skip_titles or len(text) > 80:
                continue
            href = (a.get("href") or "").strip()
            if not href or href == "#" or href.startswith("javascript:"):
                continue
            abs_url = self._make_absolute(href, url)
            if abs_url in seen_urls:
                continue
            if not self._is_chapter_url(abs_url, url):
                continue
            seen_urls.add(abs_url)
            chapter_number += 1
            chapters.append(RemoteChapter(
                source_chapter_id=abs_url,
                title=text,
                url=abs_url,
                chapter_number=chapter_number,
            ))

        return {
            "title": title,
            "author": author,
            "description": description,
            "cover": cover,
            "status": status,
            "tags": self._clean_tags(tags, title, author),
            "chapters": chapters,
        }
    @classmethod
    def _inside_navigation(cls, element: Any) -> bool:
        """Whether an element sits inside the site chrome (menu/header/footer).

        Book pages repeat the site's category menu (书库/完本/玄幻/都市/…), so
        links from those blocks must never be scraped as book tags.
        """
        node = element
        depth = 0
        while node is not None and depth < 10:
            name = getattr(node, "name", None)
            if name in ("nav", "header", "footer"):
                return True
            if name in ("body", "html") or name is None:
                return False
            tokens: list[str] = []
            classes = node.get("class") if hasattr(node, "get") else None
            if classes:
                tokens.extend(str(value) for value in classes)
            ident = ""
            if hasattr(node, "get"):
                ident = str(node.get("id") or "")
            if ident:
                tokens.append(ident)
            for token in tokens:
                parts = re.split(r"[^a-z0-9]+", token.lower())
                if any(
                    part.startswith(cls._NAV_CONTAINER_KEYWORDS)
                    for part in parts
                    if part
                ):
                    return True
            node = node.parent
            depth += 1
        return False
    def _clean_book_title(self, title: str | None) -> str:
        title = str(title or "").strip().strip("《》").strip()
        if not title:
            return ""
        for marker in self._site_markers():
            match = re.search(
                rf"[-_|\s(]*[^\-_|\n)]*{re.escape(marker)}[^\-_|\n)]*\)?$",
                title,
                re.IGNORECASE,
            )
            if not match or match.start() <= 0:
                continue
            cleaned = title[: match.start()].strip(" -_|")
            cleaned = re.sub(r"[-_|][^-_|]+$", "", cleaned).strip()
            if cleaned:
                title = cleaned
                break
        title = re.sub(
            r"[-_|]\s*(?:最新章节|全文阅读|免费阅读|小说|最新更新)\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        title = re.sub(
            r"\s+(?:作\s*者|著\s*者|author)\s*[:：].*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        # A rule like ``h2@text`` can match several elements, so the value may
        # be a newline-joined list (風月文學網 h528 returns the post title
        # followed by the sidebar headings 分站/分類/最新文章).  Keep the first
        # line that is not site chrome.
        if "\n" in title:
            chrome = {
                "分站", "分類", "分类", "最新", "最新文章", "最新章節",
                "最新章节", "首页", "首頁", "主页", "主頁", "目錄", "目录",
                "章節列表", "章节列表", "作者", "狀態", "状态", "字數", "字数",
                "簡介", "简介", "分页", "分頁",
            }
            candidates = [
                line.strip()
                for line in title.splitlines()
                if line.strip() and line.strip() not in chrome
            ]
            if candidates:
                title = candidates[0]
        title = re.sub(r"\s+", " ", title).strip()
        return title
    @staticmethod
    def _extract_labelled_title(soup: BeautifulSoup) -> str:
        """Extract a title the page explicitly labels as the work's name.

        Some sites ship no ``<title>`` at all and describe the book only as
        ``书名：X`` inside the detail card (Icu / hq555.icu does exactly that).
        Its ``ruleBookInfo.name`` is ``{{book.name}}`` -- Legado resolves that
        against the book object the search result carried, which NovelHub does
        not have when it opens a URL -- so without this the book had no name and
        ``sync_book`` rejected it ("no usable metadata") although every chapter
        parsed fine.
        """
        for meta in soup.select(
            "meta[property='og:novel:book_name'], "
            "meta[name='og:novel:book_name'], "
            "meta[property='og:novel:name']"
        ):
            content = (meta.get("content") or "").strip()
            if content:
                return content

        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.get_text(strip=True))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            name = data.get("name") if isinstance(data, dict) else None
            if name:
                return str(name).strip()

        label = (
            r"(?:书\s*名|書\s*名|小说名|小說名|漫画名|漫畫名|作品名|book\s*name)"
        )
        for node in soup.find_all(string=True):
            if node.parent is not None and node.parent.name in ("script", "style"):
                continue
            text = str(node).strip()
            if not text or len(text) > 80:
                continue
            match = re.search(
                label + r"\s*[:：]\s*([^\n<]{1,60})",
                text,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()
        return ""
    @staticmethod
    def _extract_labelled_author(soup: BeautifulSoup) -> str:
        """Extract an author that the page explicitly identifies as the work's author."""
        for meta in soup.select(
            "meta[property='og:novel:author'], "
            "meta[name='og:novel:author'], "
            "meta[property='article:author']"
        ):
            content = (meta.get("content") or "").strip()
            if content:
                return content.split(",")[0].strip()

        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.get_text(strip=True))
                author = data.get("author") if isinstance(data, dict) else None
                if isinstance(author, dict):
                    author = author.get("name")
                if author:
                    return str(author).strip()
            except (json.JSONDecodeError, AttributeError, ValueError):
                continue

        description_texts: list[str] = []
        for meta in soup.select(
            "meta[name='description'], "
            "meta[property='og:description'], "
            "meta[name='keywords'], "
            "meta[property='og:title']"
        ):
            content = (meta.get("content") or "").strip()
            if content:
                description_texts.append(content)
        for text in description_texts:
            match = re.search(
                r"由(?:作家|作者|著者)[:：]?\s*([^，。,.、|]{1,60}?)(?:创作|撰写|著|提供|，|。|,|\.|$)",
                text,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()
            match = re.search(
                r"(?:作者|著者)[:：]\s*([^，。,.、|]{1,60})",
                text,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()

        for node in soup.find_all(string=True):
            if node.parent is not None and node.parent.name in ("script", "style"):
                continue
            text = str(node).strip()
            if not text or len(text) > 80:
                continue
            match = re.search(
                r"(?:作\s*者|作者|著者|author)\s*[:：]\s*([^\n<]{1,60})",
                text,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()

        return ""
    @staticmethod
    def _extract_author_from_text(soup: BeautifulSoup) -> str:
        """Author extraction with generic document-author metadata as fallback."""
        labelled = ParsingMixin._extract_labelled_author(soup)
        if labelled:
            return labelled
        # On forum pages this usually names the submitter, so it must lose to
        # any explicitly labelled author in the title, description, or body.
        for meta in soup.select("meta[name='author']"):
            content = (meta.get("content") or "").strip()
            if content:
                return content.split(",")[0].strip()
        return ""
    def _clean_author(self, author: str | None) -> str:
        text = str(author or "").strip()
        if not text:
            return ""
        candidates: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(
                r"^(?:作\s*者|作者|著者|author)\s*[:：]\s*",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            line = re.sub(
                r"(?:作\s*者|著者|author)\s*[:：]\s*.*$",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            line = re.split(
                r"(?:字\s*数|状\s*态|分\s*类|类\s*别|更\s*新|最新章节|简介)\s*[:：]?",
                line,
                maxsplit=1,
            )[0].strip()
            line = re.sub(r"\s+", " ", line).strip(" -_|")
            for marker in self._site_markers():
                line = re.sub(
                    rf"\s*[-_|]?\s*{re.escape(marker)}.*$",
                    "",
                    line,
                    flags=re.IGNORECASE,
                ).strip()
            if line and not self._looks_like_invalid_author(line):
                candidates.append(line)
        if not candidates:
            return ""
        return min(candidates, key=len)[:120]
    @staticmethod
    def _looks_like_invalid_author(author: str | None) -> bool:
        text = str(author or "").strip()
        if not text or text.lower() in ("unknown", "未知", "暂无", "无"):
            return True
        if len(text) < 2:
            return True
        if re.fullmatch(r"\d{2,}", text):
            return True
        if re.search(r"(?:字数|状态|分类|类别)", text):
            return True
        return False
    @staticmethod
    def _split_kind_text(raw_kind: Any) -> list[str]:
        """Split a kind rule result into discrete tag candidates.

        Some sources return a whole info line such as
        `分类：都市 作者：某某 字数：10万`, which must not become one giant
        tag. Known metadata labels are turned into separators first.
        """
        if isinstance(raw_kind, (list, tuple)):
            parts: list[str] = []
            for value in raw_kind:
                parts.extend(ParsingMixin._split_kind_text(value))
            return parts
        text = str(raw_kind or "")
        text = re.sub(
            r"(?:分\s*类|类\s*别|类\s*型|字\s*数|状\s*态|作\s*者|著\s*者|更新\s*时间|简\s*介|最新章节)\s*[:：]?",
            "|",
            text,
            flags=re.IGNORECASE,
        )
        parts = [
            part.strip().strip("#").strip()
            for part in re.split(r"[,，、;；\n|]+", text)
            if part.strip().strip("#").strip()
        ]
        cleaned: list[str] = []
        for part in parts:
            if re.fullmatch(r"[\d.,]+\s*[万千]?(?:字|万|k|w)?", part, re.IGNORECASE):
                continue
            if part in ("未知", "暂无", "连载", "完结"):
                continue
            cleaned.append(part)
        return cleaned
    @classmethod
    def _clean_listing_kind(cls, title: Any) -> str:
        """Drop explore/ranking titles that are not real book categories.

        A book discovered through "周排行" is not tagged 周排行.
        """
        text = str(title or "").strip()
        if not text:
            return ""
        if cls._LISTING_ONLY_KIND_RE.search(text):
            return ""
        return text
    def _clean_tags(
        self,
        tags: list[str],
        title: str = "",
        author: str = "",
        extra_noise: list[str] | None = None,
    ) -> list[str]:
        """Drop title/author/site noise that generic parsers add as tags."""
        def _normalize(value: str) -> str:
            return re.sub(
                r"[\s《》「」『』〈〉（）【】\[\]\"'“”‘’]+",
                "",
                value,
            ).lower()

        title_norm = _normalize(title) if title and title.lower() != "unknown" else ""
        author_norm = (
            _normalize(author) if author and author.lower() != "unknown" else ""
        )
        markers = self._site_markers()
        protected_tags = {"漫画", "漫畫", "写真", "寫真", "图集", "圖集"}
        noise = {
            "tags", "tag", "标签", "分类", "类别", "类型",
            "论坛", "论坛帖子", "帖子", "书源", "书籍",
            "最新章节", "最新章节列表", "全文阅读", "免费阅读", "阅读更多",
            "书友正在看", "大家都在看", "上一章", "下一章", "目录",
            "返回目录", "首页", "开始阅读", "小说", "本站",
            # Serialisation status / site chrome that some pages expose next to
            # the real category.
            "连载", "连载中", "完结", "已完结", "完本", "全本",
            "免费小说", "在线阅读", "全文免费阅读", "手机阅读",
        }
        # Values that must never be tags even though they cannot be used as the
        # book's author (a one-character pen name such as "竹", for example, is
        # rejected by _looks_like_invalid_author but still shows up in the
        # page's keyword list).
        for value in extra_noise or []:
            value = str(value or "").strip().lower()
            if value:
                noise.add(value)
        title_noise = {"最新章节", "全文", "全文阅读", "免费阅读", "小说", "最新更新"}
        result: list[str] = []
        for tag in tags:
            tag = str(tag or "").strip().strip("#").strip()
            if not tag:
                continue
            normalized = _normalize(tag)
            if not normalized:
                continue
            # Internal ids sometimes leak in as "tags" (e.g. a 19 digit book id).
            if normalized.isdigit() and len(normalized) >= 4:
                continue
            if title_norm:
                if normalized == title_norm:
                    continue
                if title_norm in normalized:
                    remainder = _normalize(normalized.replace(title_norm, ""))
                    if not remainder:
                        continue
                    if author_norm and author_norm in remainder:
                        continue
                    if remainder in noise or any(
                        token in remainder for token in title_noise
                    ):
                        continue
            if author_norm:
                if normalized == author_norm:
                    continue
                if normalized in author_norm and len(normalized) >= 2:
                    continue
                if author_norm in normalized:
                    continue
            if tag.lower() in noise:
                continue
            if any(marker and marker.lower() in tag.lower() for marker in markers):
                continue
            if len(tag) > 20:
                continue
            result.append(tag)
        return list(dict.fromkeys(result))
    def _pick_cover_url(self, value: Any, base_url: str | None = None) -> str:
        """Pick the first usable cover URL from a rule result.

        Legado sources commonly use `img@src||fallback`, which can match every
        image on the page. A single scalar field must resolve to one URL, not a
        newline/concatenated list.
        """
        if isinstance(value, (list, tuple)):
            candidates = [str(v).strip() for v in value if str(v).strip()]
        else:
            candidates = [
                line.strip()
                for line in str(value or "").splitlines()
                if line.strip()
            ]
        base = base_url or self.base_url
        for candidate in candidates:
            if candidate.startswith(("data:", "javascript:", "about:")):
                continue
            absolute = self._make_absolute(candidate, base)
            if not absolute.startswith(("http://", "https://")):
                continue
            if self._looks_like_placeholder_cover(absolute):
                continue
            return absolute
        return ""
    @staticmethod
    def _looks_like_placeholder_cover(url: str) -> bool:
        path = urlparse(url).path.lower()
        name = path.rsplit("/", 1)[-1]
        return (
            "favicon" in path
            or path.endswith(".svg")
            or any(seg in path for seg in ("/template/", "/static/", "/images/", "/img/"))
            or name in (
                "logo.png", "logo.svg", "logo.jpg", "logo.webp",
                "default.jpg", "default.png", "nopic.jpg",
                "no-cover.jpg", "no-cover.png",
            )
        )
    @staticmethod
    def _clean_extracted_text(text: str) -> str:
        """Drop comment-count artifacts (lines that are pure short digits).

        Many chapter sites render a ``<span class=\"z count_N\">0</span>``
        comment counter inside every paragraph; ``get_text`` would include
        the counter as its own line.  A real novel paragraph is never just
        a 1-3 digit number, so such lines are dropped.
        """
        kept = []
        for line in str(text or "").split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if re.fullmatch(r"\d{1,3}", stripped):
                continue
            kept.append(stripped)
        return "\n".join(kept).strip()
    @staticmethod
    def _looks_like_rule_diagnostic(content: str, html: str) -> bool:
        """Do not persist failed script output as a chapter body."""
        value = str(content or "").strip()
        if not value:
            return True
        if value == str(html or "").strip():
            return True
        return any(marker in value for marker in (
            "org.jsoup", "Packages.", "java.lang.", "ReferenceError:",
        ))
    @staticmethod
    def _content_type_tags(*values: Any) -> list[str]:
        """Keep non-novel forum posts discoverable in the existing library."""
        text = " ".join(str(value or "") for value in values).lower()
        tags: list[str] = []
        if "漫画" in text or "漫畫" in text:
            tags.append("漫画")
        if "写真" in text or "寫真" in text:
            tags.append("写真")
        if any(marker in text for marker in ("图集", "圖集", "图包", "圖包", "套图", "套圖")):
            tags.append("图集")
        return tags
    def _uses_android_js_rule(self, section: str, field: str) -> bool:
        """Identify Legado rules that need Android/JVM-only APIs.

        ``org.jsoup`` is emulated by the Node shim and ``java.*`` has a
        stub, so only raw ``Packages.*`` JVM calls remain unsupported.
        """
        rule = (self.config.get(section) or {}).get(field, "")
        text = str(rule or "")
        if "Packages." in text and "org.jsoup" not in text:
            return True
        return any(marker in text for marker in (
            "android.", "androidx.", "javax.swing.", "org.json.JSONObject",
        ))
    def _parse_chapter_content_generic(self, html: str, base_url: str = "") -> str:
        """Extract readable text when the configured content rule misses.

        Manga / photo sources have chapter pages without any text container:
        their content is one or more images.  Falling back to those images
        (as markdown, which the reader renders) keeps such chapters readable
        instead of ending in ``Chapter returned empty content``.
        """
        soup = BeautifulSoup(html, "lxml")
        # Gallery pages often contain an ad image before the real page.  Prefer
        # the site's own content-image containers so the ad never becomes the
        # chapter and pagination can follow one primary image at a time.
        primary_images = self._gallery_primary_images(soup, base_url)
        if primary_images:
            return "\n".join(primary_images)
        content_selectors = (
            "#content-section pre",
            "#content-section",
            ".content-section pre",
            ".content-section",
            ".chapter_content_box",
            "#content",
            "#chapter-content",
            ".page-content pre",
            ".page-content",
            "article",
            "div.content",
            ".chapter-content",
            ".read-content",
            ".reader-content",
            ".article",
            "main",
            ".post-content",
            ".entry-content",
            ".article-content",
            ".read-main",
            "#read-content",
            ".book-content",
            ".text-content",
            ".novel-content",
            ".readContent",
            ".Readarea",
            "#chapter",
            ".chapter",
            ".txt",
        )
        for selector in content_selectors:
            el = soup.select_one(selector)
            if el is None:
                continue
            for tag in el.find_all(["script", "style", "ins", "nav", "header", "footer"]):
                tag.decompose()
            text = self._clean_extracted_text(
                self._content_text_preserving_images(str(el))
            )
            if text:
                return text
        images = self._chapter_images(soup, base_url)
        if images:
            return "\n".join(images)
        return ""
    @staticmethod
    def _content_text_preserving_images(content: str) -> str:
        """Convert parsed chapter HTML to text while keeping image references."""
        soup = BeautifulSoup(content, "lxml")
        for tag in soup.find_all(
            ["script", "style", "ins", "nav", "header", "footer", "iframe"],
        ):
            tag.decompose()
        for img in soup.find_all("img"):
            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-original")
                or ""
            ).strip()
            if not src:
                img.decompose()
                continue
            alt = img.get("alt") or ""
            img.replace_with(f"![{alt}]({src})")
        return ParsingMixin._clean_extracted_text(soup.get_text("\n", strip=True))
    @staticmethod
    def _dedupe_chapters(
        chapters: list["RemoteChapter"],
        book_url: str,
    ) -> list["RemoteChapter"]:
        """Deduplicate chapters by URL and by repeated title on the same book.

        A broad generic TOC scanner often sees the same chapter twice (for
        example a "start reading" button plus the real list entry, or a link
        ending in `/0.html` alongside the canonical hash URL). When the same
        normalized title appears under the same parent path, keep the
        canonical-looking URL.
        """
        def normalized_url(url: str) -> str:
            return url.split("#", 1)[0].rstrip("/")

        def parent_path(url: str) -> str:
            path = urlparse(url).path.rstrip("/")
            return path.rsplit("/", 1)[0] if "/" in path else path

        def normalized_title(title: str) -> str:
            return re.sub(
                r"[\s《》「」『』〈〉（）【】\[\]\"'“”‘’]+",
                "",
                title or "",
            ).lower()

        seen_urls: set[str] = set()
        by_title: dict[str, list[RemoteChapter]] = {}
        result: list[RemoteChapter] = []

        for chapter in chapters:
            key = normalized_url(chapter.url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            title_key = normalized_title(chapter.title)
            if title_key:
                by_title.setdefault(title_key, []).append(chapter)
            result.append(chapter)

        final: list[RemoteChapter] = []
        seen_kept: set[str] = set()
        for chapter in result:
            if id(chapter) in seen_kept:
                continue
            title_key = normalized_title(chapter.title)
            group = by_title.get(title_key) or [chapter]
            same_path_groups: dict[str, list[RemoteChapter]] = {}
            for ch in group:
                same_path_groups.setdefault(parent_path(ch.url), []).append(ch)
            duplicate_group = next(
                (
                    same_group
                    for same_group in same_path_groups.values()
                    if len(same_group) > 1
                ),
                None,
            )
            if duplicate_group is None:
                final.append(chapter)
                seen_kept.add(id(chapter))
                continue

            def _canonical_score(ch: RemoteChapter) -> tuple[int, int]:
                path = urlparse(ch.url).path.rstrip("/")
                last = path.rsplit("/", 1)[-1].lower()
                if last in ("0", "0.html"):
                    return (0, len(path))
                if re.fullmatch(r"[0-9a-f]{8,}", last.rsplit(".", 1)[0], re.I):
                    return (2, len(path))
                return (1, len(path))

            best = max(duplicate_group, key=_canonical_score)
            if chapter is best:
                final.append(chapter)
                for ch in group:
                    seen_kept.add(id(ch))
            else:
                seen_kept.add(id(chapter))
        return final
    @staticmethod
    def _dedupe_content_images(content: str) -> str:
        """Drop duplicate image-only lines while preserving text and order."""
        lines: list[str] = []
        seen: set[str] = set()
        image_line = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)$")
        for line in str(content or "").splitlines():
            stripped = line.strip()
            match = image_line.match(stripped)
            if match:
                key = match.group(1).split("#", 1)[0]
                if key in seen:
                    continue
                seen.add(key)
            lines.append(line)
        return "\n".join(lines).strip()

    # ---- series index (合集帖) ------------------------------------------
    #
    # Forum sources model a post as a one-chapter book, so a post that merely
    # *indexes* a work ("【忘尘山:高冷仙子皆为炉鼎】1-23") becomes a book whose
    # single chapter holds just the first instalment: the source's ``ruleToc``
    # matched the thread title, and the remaining parts are only reachable
    # through the links the post body itself carries.  Everything below turns
    # those links into chapters, so one book stops meaning "one part".

    @staticmethod
    def _title_key(text: str) -> str:
        """Comparison key for a title: letters/digits/CJK only, lowercased.

        Drops the punctuation that differs between a site's heading and its
        link text (``：`` vs ``:``, ``（）`` vs ``()``, spaces).
        """
        return "".join(char for char in str(text or "").lower() if char.isalnum())

    @staticmethod
    def _common_key_prefix(keys: list[str]) -> str:
        """Longest prefix shared by every key."""
        if not keys:
            return ""
        prefix = keys[0]
        for key in keys[1:]:
            limit = min(len(prefix), len(key))
            index = 0
            while index < limit and prefix[index] == key[index]:
                index += 1
            prefix = prefix[:index]
            if not prefix:
                break
        return prefix

    @staticmethod
    def _key_prefix_tail(text: str, prefix_key: str) -> str:
        """Return what ``text`` says after its first ``prefix_key`` key chars."""
        if not prefix_key:
            return str(text or "")
        raw = str(text or "")
        seen = 0
        for index, char in enumerate(raw):
            if char.isalnum():
                seen += 1
                if seen == len(prefix_key):
                    return raw[index + 1:]
        return ""

    def _content_body_elements(self, html: str) -> list[Any]:
        """Elements the source's own content rule treats as the post body.

        Anchoring on ``ruleContent.content`` is what separates the parts of a
        work from the page chrome: a "相关推荐" box holds links too, and only the
        body the source itself reads for the chapter may become chapters.
        """
        engine = getattr(self, "engine", None)
        if engine is None:
            return []
        rule = str(
            (self.config.get("ruleContent") or {}).get("content") or ""
        ).strip()
        # A ``<js>``/``@js:`` content rule produces values, not elements.
        if not rule or self._uses_android_js_rule("ruleContent", "content"):
            return []
        if rule.lower().startswith("@css:"):
            rule = rule[5:].strip()
        steps = YueduRuleEngine._split_element_steps(rule)
        if not steps:
            return []
        try:
            return list(engine._get_elements(html, steps[0]))
        except Exception:
            # A rule the engine cannot select is simply not usable here; the
            # book keeps the single-chapter shape it had before.
            return []

    def _series_index_parts(
        self,
        html: str,
        page_url: str,
        book_title: str,
    ) -> list[dict[str, str]]:
        """Other parts of this work, as linked from inside the post body.

        Returns ``[{"title", "url"}]`` in reading order (the announced part
        number), or an empty list when the body does not carry a series index.
        """
        body_elements = self._content_body_elements(html)
        if not body_elements:
            return []
        book_key = self._title_key(book_title)
        if len(book_key) < SERIES_INDEX_PREFIX_MIN:
            return []
        page_host = urlparse(page_url).netloc.lower()
        page_identity = page_url.split("#", 1)[0].rstrip("/")

        candidates: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for element in body_elements:
            try:
                anchors = element.select("a[href]")
            except Exception:
                continue
            for anchor in anchors:
                href = str(anchor.get("href") or "").strip()
                title = anchor.get_text(" ", strip=True)
                if not href or not title or title in SERIES_INDEX_SKIP_TEXTS:
                    continue
                if self._inside_navigation(anchor):
                    continue
                target = urljoin(page_url, href).split("#", 1)[0]
                if not target.startswith(("http://", "https://")):
                    continue
                if urlparse(target).netloc.lower() != page_host:
                    continue
                if target.rstrip("/") == page_identity or target in seen_urls:
                    continue
                key = self._title_key(title)
                if len(key) < 2:
                    continue
                seen_urls.add(target)
                candidates.append({"title": title, "url": target, "key": key})

        if len(candidates) < 2:
            return []
        # Every part repeats the work's own name and differs only in its part
        # marker, so the shared prefix must be long *and* belong to this book.
        prefix = self._common_key_prefix([item["key"] for item in candidates])
        if len(prefix) < SERIES_INDEX_PREFIX_MIN or not book_key.startswith(prefix):
            return []

        parts: list[dict[str, Any]] = []
        for candidate in candidates:
            tail = self._key_prefix_tail(candidate["title"], prefix)
            if len(self._title_key(tail)) > SERIES_INDEX_PART_TAIL_MAX:
                continue
            numbers = re.findall(r"\d{1,4}", tail)
            if not numbers:
                continue
            parts.append({
                "title": candidate["title"],
                "url": candidate["url"],
                "order": (int(numbers[0]), int(numbers[-1])),
            })
        if len(parts) < 2:
            return []
        # The index lists the newest part first; reading order is the announced
        # part number.  ``sort`` is stable, so equal orders keep page order.
        parts.sort(key=lambda item: item["order"])
        return [
            {"title": str(item["title"]), "url": str(item["url"])}
            for item in parts[:SERIES_INDEX_MAX_PARTS]
        ]
