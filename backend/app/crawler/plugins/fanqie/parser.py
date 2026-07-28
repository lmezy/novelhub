"""Fanqie parser -- BeautifulSoup selectors for fanqienovel.com."""

import re
from bs4 import BeautifulSoup
from app.crawler.base import RemoteBook, RemoteChapter


class FanqieParser:
    SELECTORS = {
        "book_title": "h1, .info-name, .book-name, .title",
        "book_author": ".author-name, .writer, .author, .info-writer a",
        "book_description": ".description, .book-desc, .intro, .info-desc",
        "book_status": ".tag, .info-status, .status",
        "book_tags": ".tag-item, .tag, .category-tag",
        "chapter_list": ".chapter-item, .catalog-item, .chapter-list a, .catalog a",
        "chapter_content": ".chapter-content, .article, .content, .reader-content",
    }

    def parse_book(self, html: str, url: str) -> RemoteBook:
        soup = BeautifulSoup(html, "lxml")
        s = self.SELECTORS
        title = self._text(soup, s["book_title"]) or "Unknown"
        author = self._text(soup, s["book_author"]) or "Unknown"
        desc = self._text(soup, s["book_description"])
        st = self._text(soup, s["book_status"]) or "ongoing"
        status = "completed" if any(w in st for w in ["wanjie", "wanben", "completed", "finished", "completed"]) else "ongoing"
        bid = re.search(r"/page/(\d+)", url)
        book_id = bid.group(1) if bid else url.rstrip("/").split("/")[-1]
        chapters = []
        items = soup.select(s["chapter_list"])
        for i, item in enumerate(items, 1):
            link = item if item.name == "a" else item.select_one("a")
            if not link:
                continue
            href = link.get("href", "")
            if not href.strip():
                continue
            ch_id = re.search(r"/(\d+)", href)
            cid = ch_id.group(1) if ch_id else href.rstrip("/").split("/")[-1]
            chapters.append(RemoteChapter(
                source_chapter_id=cid,
                title=link.get_text(strip=True),
                url=self._abs(href),
                chapter_number=i,
            ))
        tags = self._tags(soup)
        return RemoteBook(source_book_id=book_id, title=title, author=author,
                          description=desc, status=status, chapters=chapters, tags=tags)

    def parse_chapter_content(self, html: str):
        soup = BeautifulSoup(html, "lxml")
        ce = soup.select_one(self.SELECTORS["chapter_content"])
        if ce is None:
            return "Untitled", ""
        for t in ce.find_all(["script", "style", "ins"]):
            t.decompose()
        paras = [p.get_text(strip=True) for p in ce.find_all(["p", "div"])
                 if len(p.get_text(strip=True)) > 5]
        if not paras:
            text = ce.get_text(chr(10), strip=True)
            paras = [p for p in text.split(chr(10)) if p.strip()]
        te = soup.select_one("h1, h2, h3, .chapter-title, .title")
        title = te.get_text(strip=True) if te else "Untitled"
        return title, chr(10).join(paras)

    def _text(self, soup, sel):
        el = soup.select_one(sel)
        return el.get_text(strip=True) if el else None

    def _tags(self, soup):
        els = soup.select(self.SELECTORS["book_tags"])
        return list({el.get_text(strip=True) for el in els if el.get_text(strip=True)})

    @staticmethod
    def _abs(href, base="https://fanqienovel.com"):
        if href.startswith("http"):
            return href
        if href.startswith("//"):
            return "https:" + href
        return base + href if href.startswith("/") else base + "/" + href