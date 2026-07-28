"""Qidian parser -- BeautifulSoup selectors for qidian.com."""

import re
from typing import Optional
from bs4 import BeautifulSoup
from app.crawler.base import RemoteBook, RemoteChapter


class QidianParser:
    SELECTORS = {
        "book_title": "h1 em, .book-info h1 em, .book-name",
        "book_author": "a.writer, .book-info .writer",
        "book_description": "div.book-intro p, .intro, .book-desc",
        "book_status": "p.book-status, .tag-blue",
        "book_tags": "a.tag, span.tag-item, .tag-wrap a",
        "chapter_list": "#j-catalogWrap li, .catalog-content-wrap li",
        "chapter_link": "a",
        "chapter_content": "div.read-content, div#chapter-content",
    }

    def parse_book(self, html: str, url: str) -> RemoteBook:
        soup = BeautifulSoup(html, "lxml")
        s = self.SELECTORS
        title = self._text(soup, s["book_title"]) or "Unknown"
        author = self._text(soup, s["book_author"]) or "Unknown"
        desc = self._text(soup, s["book_description"])
        st = self._text(soup, s["book_status"]) or "ongoing"
        status = "completed" if any(w in st for w in ["wanjie","wanben","completed"]) else "ongoing"
        bid = re.search(r"/book/(\d+)", url)
        book_id = bid.group(1) if bid else url.split("/")[-1]
        chapters = []
        items = soup.select(s["chapter_list"])
        for i, item in enumerate(items, 1):
            link = item.select_one(s["chapter_link"])
            if not link: continue
            href = link.get("href", "")
            cid = re.search(r"/(\d+)[./]?", href)
            ch_id = cid.group(1) if cid else href.rstrip("/").split("/")[-1].split(".")[0]
            chapters.append(RemoteChapter(
                source_chapter_id=ch_id,
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
        if ce is None: return "Untitled", ""
        for t in ce.find_all(["script","style","ins"]): t.decompose()
        paras = [p.get_text(strip=True) for p in ce.find_all(["p","div"]) if len(p.get_text(strip=True))>5]
        if not paras:
            text = ce.get_text(chr(10), strip=True)
            paras = [p for p in text.split(chr(10)) if p.strip()]
        te = soup.select_one("h3.j_chapterName, .chapter-name, h2")
        title = te.get_text(strip=True) if te else "Untitled"
        return title, chr(10).join(paras)

    def _text(self, soup, sel):
        el = soup.select_one(sel)
        return el.get_text(strip=True) if el else None

    def _tags(self, soup):
        els = soup.select(self.SELECTORS["book_tags"])
        return list({el.get_text(strip=True) for el in els if el.get_text(strip=True)})

    @staticmethod
    def _abs(href, base="https://www.qidian.com"):
        if href.startswith("http"): return href
        if href.startswith("//"): return "https:"+href
        return base+href if href.startswith("/") else base+"/"+href
