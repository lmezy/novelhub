import json
import re
import time

import meilisearch
from loguru import logger

from app.core.config import settings
from app.services.book_kind import KIND_NOVEL, normalize_kind


class SearchService:
    INDEX_BOOKS = "books"
    INDEX_CHAPTERS = "chapters"
    CHAPTER_BUFFER_SIZE = 100
    # Advanced search fetches a candidate window per condition, scores it in
    # Python and then slices the page out of it.  The window therefore decides
    # how long *every* page click takes: at 5000 candidates a single search on
    # the live 115k-chapter index regularly took 40-110 s (~66 s cold), because
    # Meilisearch drops its per-query cache whenever the crawler indexes a new
    # chapter batch.  1000 keeps the same semantics at ~0.5 s while still
    # offering 25 pages of 40 results.
    CANDIDATE_LIMIT = 1000
    # ``content`` needs its own, much smaller window.  Scoring a content match
    # requires the chapter text itself, so the candidate fetch has to carry it:
    # 1000 chapters is ~96 MB of JSON and took 18-25 s *on every request*
    # (measured on the live 115k-chapter index), while 300 is ~29 MB and stays
    # under a second.  300 candidates is still eight pages of 40 results.
    #
    # That window used to be the *hard* end of a 正文 search: with 300 candidates
    # scored and ranked, page 9 did not exist and the result list simply stopped,
    # which read as "搜索只有 300 条" (the reported bug).  It now grows with the
    # page being asked for (``_content_window_for``), in the same 300-candidate
    # chunks, up to ``CONTENT_WINDOW_MAX``; the first eight pages stay as cheap
    # as they were and only an actual deep page pays for the wider scan.
    CONTENT_CANDIDATE_LIMIT = 300
    # How many 正文 candidates one page may scan through.  2000 chapters is
    # ~190 MB of JSON and some ten seconds, which is the point where a deep page
    # stops being worth it -- and, measured against the live index, enough to
    # answer "I only wanted to look further than page 8".  A page beyond this is
    # served from the last rows of the window that was scanned.
    CONTENT_WINDOW_MAX = 2000
    # Per-request candidate count while walking a window wider than one engine
    # page (see ``_scan_condition``).  Meilisearch's default ``pagination
    # .maxTotalHits`` is 1000, so a window of at most that size is still exactly
    # one request and only the 1500/2000-deep pages are split.
    _SCAN_CHUNK = 1000
    # Single-condition searches score the engine's candidates in Python (see
    # ``_single_condition_search``) because Meilisearch cannot express a Chinese
    # substring match: charabia splits 白骨精 into 白/骨/精 and
    # ``matchingStrategy: "last"`` only requires the last fragment, so 穿成白骨肿么破
    # matches on 破, ``matchingStrategy: "all"`` returns 0 hits for 剑来 (which has
    # 113 real ones) and phrase queries are no better.  The scan only retrieves
    # ``id`` + the searched field, which is 0.02-0.7 s for 10 000 documents, and
    # the ordered result is cached for repeat pages.
    METADATA_CANDIDATE_LIMIT = 10_000
    # Meilisearch's ``pagination.maxTotalHits`` -- the highest page number its
    # ranking rules will serve (it refuses ``page * hitsPerPage`` beyond this).
    # Both indexes are created with this value (``_ensure_index``).
    MAX_TOTAL_HITS = 10_000
    # How long a scored single-condition result stays usable for paging.  Books
    # and chapters keep flowing in while a user pages, so this is deliberately
    # short; the cost of a miss is one scan.
    PAGE_CACHE_TTL_SECONDS = 120
    # One entry is at most 10 000 ``(score, id, snippet)`` pairs.  The ids and
    # scores dominate the metadata case (~1.5 MB), so 16 keeps the worst case
    # around 24 MB per process.  A 正文 entry is smaller than that by design: its
    # window caps at ``CONTENT_WINDOW_MAX`` rows and each row carries a bounded
    # excerpt, so even sixteen deep-page scans stay in the low tens of MB.
    PAGE_CACHE_MAX_ENTRIES = 16
    # Multi-condition (AND/OR) searches used to score every condition on its own
    # candidate window and intersect the ids in Python.  For two conditions on
    # the same attribute that is arithmetically hopeless: 正文 「铃」 has 9 619
    # matching chapters and 正文 「仙」 has 10 000+, but their *top* 300 candidates
    # barely overlap, so `正文 铃 AND 正文 仙` returned 0 while the real
    # intersection held 1 299 chapters.  Same-attribute AND now asks the engine
    # for the conjunction (`matchingStrategy: "all"`) instead, which finds real
    # intersections at any depth.  The window below is how many of those ranked
    # hits are scored and cached for paging; it is much larger than the
    # per-condition window because the engine did the selection already.
    CONJUNCTION_CANDIDATE_LIMIT = 1000
    # Page hits are re-checked against the real field text (the engine's CJK
    # matching drops a term silently sometimes).  The check needs the stored
    # field, so it is capped: pages beyond this many hits come from the engine's
    # own match, still ranked, just not re-verified.
    CONJUNCTION_VERIFY_MAX_HITS = 200
    # Words returned around the match when a chapter body is used as a snippet.
    SNIPPET_CROP_WORDS = 60
    # A served snippet is a window anchored *at* the match rather than centred on
    # it.  The result list clamps a snippet to two lines, and a phone line holds
    # roughly a third of a desktop line's glyphs (a 320 px viewport fits ~21 CJK
    # glyphs per line at ``text-xs``), so a window that merely *contains* the
    # match -- Meilisearch's own crop puts it around the midpoint -- shows the
    # searched word on the desktop and hides it on the phone.  That asymmetry is
    # the "the result does not contain what I searched for" report.  The lead
    # below plus the ``...`` marker stay inside the first phone line; the tail is
    # the part the clamp trims first, so it can be generous.
    SNIPPET_LEAD_CHARS = 12
    SNIPPET_TAIL_CHARS = 160
    CONTENT_INDEX_LIMIT = 100_000
    DESCRIPTION_INDEX_LIMIT = 2000

    # ``content`` is deliberately absent from both lists: one chapter document
    # can carry 100 KB of text, and retrieving it for every candidate made a
    # title search move 313 MB and take ~38 s (measured against the live index).
    # Only a search *on* ``content`` asks for it (``_search_field`` appends the
    # searched attribute).
    BOOK_RETRIEVE_ATTRS = [
        "id",
        "title",
        "author",
        "description",
        "status",
        "source_id",
        "author_id",
        "is_r18",
        "tags",
        "category_names",
        "kind",
    ]
    CHAPTER_RETRIEVE_ATTRS = [
        "id",
        "book_id",
        "title",
        "chapter_number",
        "book_title",
        "book_author",
        "book_description",
        "is_r18",
        "tags",
        "category_names",
        "kind",
    ]

    BOOK_FIELD_ATTRS = {
        "title": "title",
        "author": "author",
        "description": "description",
        "tags": "tags",
        "category": "category_names",
    }
    CHAPTER_BOOK_FIELD_ATTRS = {
        "title": "book_title",
        "author": "book_author",
        "description": "book_description",
        "tags": "tags",
        "category": "category_names",
    }
    CHAPTER_FIELD_ATTRS = {
        "chapter_title": "title",
        "content": "content",
    }
    SEARCHABLE_BOOKS = [
        "title",
        "author",
        "description",
        "status",
        "tags",
        "category_names",
    ]
    SEARCHABLE_CHAPTERS = [
        "title",
        "book_title",
        "book_author",
        "book_description",
        "content",
        "tags",
        "category_names",
    ]
    FILTERABLE_ATTRIBUTES = [
        # ``id`` is filterable so a single page can be hydrated back from a
        # cached ranked id list (``filter: id IN [...]``).
        "id",
        "source_id",
        "book_id",
        "author_id",
        "is_r18",
        "tags",
        "kind",
    ]

    def __init__(self):
        self.client = meilisearch.Client(settings.MEILI_HOST, settings.MEILI_KEY)
        self._chapter_buffer: list[dict] = []
        self._ensured: set[str] = set()
        self._page_cache: dict[str, tuple[float, list[tuple[int, str, str]]]] = {}
        # The engine's own hit count for a conjunction query.  It has to be kept
        # with the cached ranking, otherwise page 1 would report the engine's
        # count and page 2 the window's -- the result list would "change" as the
        # user pages, which is exactly the 0-result complaint this replaces.
        self._conjunction_totals: dict[str, tuple[float, int | None]] = {}

    def _ensure_index(self, name: str, primary_key: str = "id") -> None:
        if name in self._ensured:
            return
        try:
            self.client.get_index(name)
        except meilisearch.errors.MeilisearchApiError:
            self.client.create_index(name, {"primaryKey": primary_key})
        index = self.client.index(name)
        index.update_filterable_attributes(self.FILTERABLE_ATTRIBUTES)
        if name == self.INDEX_BOOKS:
            index.update_searchable_attributes(self.SEARCHABLE_BOOKS)
        else:
            index.update_searchable_attributes(self.SEARCHABLE_CHAPTERS)
        try:
            index.update_pagination_settings({"maxTotalHits": self.MAX_TOTAL_HITS})
        except Exception:
            pass
        self._ensured.add(name)

    def ensure_indexes(self) -> None:
        """Create the books/chapters indexes if they do not exist yet."""
        self._ensure_index(self.INDEX_BOOKS)
        self._ensure_index(self.INDEX_CHAPTERS)

    def index_book(self, book: dict) -> None:
        self._ensure_index(self.INDEX_BOOKS)
        doc = dict(book)
        # Every book document must carry ``kind``: it is what keeps a search
        # started on /novels or /comics inside its own half of the library, and
        # a document without the field matches neither side of the filter.
        doc["kind"] = normalize_kind(doc.get("kind"))
        self.client.index(self.INDEX_BOOKS).add_documents([doc])
        logger.debug("Indexed book {}", doc.get("id"))

    def index_chapter(self, chapter: dict) -> None:
        self._ensure_index(self.INDEX_CHAPTERS)
        self.client.index(self.INDEX_CHAPTERS).add_documents([chapter])
        logger.debug("Indexed chapter {}", chapter.get("id"))

    def update_book_tags(self, book_id: str, tags: list[str]) -> None:
        self._ensure_index(self.INDEX_BOOKS)
        self.client.index(self.INDEX_BOOKS).add_documents([{"id": book_id, "tags": tags}])

    def update_chapter_tags(self, chapter_id: str, tags: list[str]) -> None:
        self._ensure_index(self.INDEX_CHAPTERS)
        self.client.index(self.INDEX_CHAPTERS).add_documents([
            {"id": chapter_id, "tags": tags}
        ])

    def buffer_chapter(self, chapter: dict) -> None:
        """Queue a chapter for batched indexing, flushing in chunks."""
        self._ensure_index(self.INDEX_CHAPTERS)
        self._chapter_buffer.append(chapter)
        if len(self._chapter_buffer) >= self.CHAPTER_BUFFER_SIZE:
            self.flush_chapters()

    def flush_chapters(self) -> None:
        """Send queued chapter documents to Meilisearch in one batch."""
        if not self._chapter_buffer:
            return
        docs = self._chapter_buffer
        self._chapter_buffer = []
        try:
            self.client.index(self.INDEX_CHAPTERS).add_documents(docs)
            logger.debug("Indexed {} chapters", len(docs))
        except Exception as exc:
            logger.warning("Failed to index {} chapters: {}", len(docs), exc)

    @staticmethod
    def _visibility_filter(allow_r18: bool, allow_all_ages: bool) -> str | None:
        if allow_r18 and allow_all_ages:
            return None
        if allow_r18:
            return "is_r18 = true"
        if allow_all_ages:
            return "is_r18 = false"
        return "is_r18 = true AND is_r18 = false"

    @staticmethod
    def _tag_filter(tag: str | None) -> str | None:
        if not tag:
            return None
        safe_tag = tag.replace('"', '\\"')
        return f'tags = "{safe_tag}"'

    @staticmethod
    def _kind_filter(kind: str | None) -> str | None:
        """Restrict a search to novels or comics.

        ``books.kind`` is the same derived field ``/books/browse?kind=`` uses;
        it is indexed so ``/novels`` and ``/comics`` search their own half of
        the library instead of mixing both (the search index used to ignore it
        completely).
        """
        value = str(kind or "").strip().lower()
        if value not in ("novel", "comic"):
            return None
        return f'kind = "{value}"'

    def _combined_filter(
        self,
        allow_r18: bool,
        allow_all_ages: bool,
        tag: str | None,
        source_id: str | None = None,
        kind: str | None = None,
    ) -> str | None:
        parts = []
        r18_filter = self._visibility_filter(allow_r18, allow_all_ages)
        if r18_filter:
            parts.append(r18_filter)
        tag_part = self._tag_filter(tag)
        if tag_part:
            parts.append(tag_part)
        if source_id:
            safe_source = source_id.replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'source_id = "{safe_source}"')
        kind_part = self._kind_filter(kind)
        if kind_part:
            parts.append(kind_part)
        return " AND ".join(parts) if parts else None

    def _retrieve_attrs(self, index_name: str, attr: str) -> list[str]:
        """Fields to return for candidate scoring (never ``content`` unless asked)."""
        base = (
            self.BOOK_RETRIEVE_ATTRS
            if index_name == self.INDEX_BOOKS
            else self.CHAPTER_RETRIEVE_ATTRS
        )
        attrs = list(base)
        if attr and attr not in attrs:
            attrs.append(attr)
        return attrs

    def search_books(
        self,
        query: str,
        *,
        offset: int = 0,
        limit: int = 20,
        allow_r18: bool = True,
        allow_all_ages: bool = True,
        tag: str | None = None,
        source_id: str | None = None,
        kind: str | None = None,
    ) -> dict:
        self._ensure_index(self.INDEX_BOOKS)
        self._ensure_index(self.INDEX_CHAPTERS)
        options = {
            "offset": offset,
            "limit": limit,
            "attributesToSearchOn": [
                "title",
                "author",
                "description",
                "tags",
                "category_names",
            ],
        }
        filters = self._combined_filter(allow_r18, allow_all_ages, tag, source_id, kind)
        if filters:
            options["filter"] = filters
        try:
            return self.client.index(self.INDEX_BOOKS).search(query, options)
        except meilisearch.errors.MeilisearchApiError:
            options.pop("attributesToSearchOn", None)
            return self.client.index(self.INDEX_BOOKS).search(query, options)

    def search_chapters(
        self,
        query: str,
        *,
        offset: int = 0,
        limit: int = 20,
        allow_r18: bool = True,
        allow_all_ages: bool = True,
        tag: str | None = None,
        source_id: str | None = None,
        kind: str | None = None,
    ) -> dict:
        self._ensure_index(self.INDEX_BOOKS)
        self._ensure_index(self.INDEX_CHAPTERS)
        options = {
            "offset": offset,
            "limit": limit,
            "attributesToSearchOn": [
                "title",
                "book_title",
                "book_author",
                "book_description",
                "content",
                "tags",
                "category_names",
            ],
        }
        filters = self._combined_filter(allow_r18, allow_all_ages, tag, source_id, kind)
        if filters:
            options["filter"] = filters
        try:
            return self.client.index(self.INDEX_CHAPTERS).search(query, options)
        except meilisearch.errors.MeilisearchApiError:
            options.pop("attributesToSearchOn", None)
            return self.client.index(self.INDEX_CHAPTERS).search(query, options)

    @staticmethod
    def _longest_run(needle: str, haystack: str) -> int:
        """Length of the longest contiguous piece of ``needle`` present in text.

        Only used to order fuzzy hits: a title holding 「铃铛」 as one piece beats
        one where 铃 and 铛 sit far apart.  Queries come from a search box, so the
        O(n·m) walk is bounded by a handful of characters and stops as soon as a
        start position cannot beat the best run found so far.
        """
        if not needle or not haystack:
            return 0
        best = 0
        for start in range(len(needle)):
            if len(needle) - start <= best:
                break
            end = start
            while end < len(needle) and needle[start:end + 1] in haystack:
                end += 1
            best = max(best, end - start)
        return best

    @staticmethod
    def _condition_score(
        value: str,
        text: str,
        mode: str,
        ranking_score: float = 0.0,
    ) -> int:
        """Score one field condition. Exact requires the whole string to appear.

        Fuzzy requires **every character** of the query to appear (any order, gaps
        allowed) and then ranks the survivors: whole string first, then the
        longest contiguous piece, then the engine's own ranking.  Partial matches
        are *not* results: Meilisearch's CJK analysis is per-character, so
        ``matchingStrategy: "last"`` only needs the last character, and 铃铛 came
        back with 19 hits of which exactly one contained 铃铛 (the rest had just
        铃 -- 铃木/铃雨/聖誕鈴聲 -- or just 铛).  Users read that as "结果和搜索内容
        没有关系", which is what it is.
        """
        if isinstance(text, (list, tuple)):
            text = " ".join(str(item) for item in text)
        needle = value.strip().lower()
        haystack = (text or "").lower()
        if not needle or not haystack:
            return 0
        tie = int(max(0.0, min(float(ranking_score or 0.0), 1.0)) * 1000)
        if mode == "exact":
            return 1_000_000 + tie if needle in haystack else 0

        chars = list(dict.fromkeys(ch for ch in value if not ch.isspace()))
        if not chars:
            return 0
        matched = sum(1 for ch in chars if ch.lower() in haystack)
        if matched < len(chars):
            return 0
        full_bonus = 1000 if needle in haystack else 0
        # Capped below ``full_bonus`` so an exact hit always outranks a scattered
        # one, however long the query is.
        run_bonus = min(SearchService._longest_run(needle, haystack), 99) * 10
        return matched * 10_000 + full_bonus + run_bonus + tie

    def _search_field(
        self,
        index_name: str,
        attr: str,
        value: str,
        filters: str | None,
        limit: int | None = None,
    ) -> dict:
        options = {
            "limit": int(
                limit
                or (self.CONTENT_CANDIDATE_LIMIT if attr == "content"
                    else self.CANDIDATE_LIMIT)
            ),
            "offset": 0,
            "attributesToSearchOn": [attr],
            # Cap the payload: without this a chapter search returned every
            # candidate's full 100 KB body (313 MB for one page click).
            "attributesToRetrieve": self._retrieve_attrs(index_name, attr),
            "showRankingScore": True,
        }
        if filters:
            options["filter"] = filters
        try:
            return self.client.index(index_name).search(value, options)
        except meilisearch.errors.MeilisearchApiError:
            # Older indexes may not expose the new fields yet; fall back to a
            # broader candidate fetch and rely on post-filtering/scoring.
            options.pop("attributesToSearchOn", None)
            return self.client.index(index_name).search(value, options)

    def _candidate_window(self, index_name: str, attr: str) -> int:
        """How many candidates one condition may pull into the Python scorer.

        This is a *correctness* knob, not just a speed one.  Every condition is
        scored in Python, so whatever falls outside the window is invisible to it:
        ``category = 言情`` alone matches 1 847 books, so with the old flat
        1 000-candidate window ``title ~ 晴晴的 AND category = 言情`` returned 0
        although the book carries both -- and every other AND/OR condition whose
        partner ranked below the cut behaved the same way ("多条件搜索全是 0 条").

        The books index answers a 10 000-document window in 0.1-0.3 s (measured
        on the live 24k-book index), so book-side conditions simply score every
        match.  The chapters index is two orders of magnitude slower at that
        width (47-250 s measured on the live 115k-chapter index), so chapter-side
        conditions keep the small window, and ``content`` keeps the smallest one
        because its candidates have to carry the body (``_retrieve_attrs``).
        """
        if attr == "content":
            return self.CONTENT_CANDIDATE_LIMIT
        if index_name == self.INDEX_BOOKS:
            return self.METADATA_CANDIDATE_LIMIT
        return self.CANDIDATE_LIMIT

    def _search_all_with_filter(self, index_name: str, filters: str | None) -> dict:
        options = {
            "limit": self.CANDIDATE_LIMIT,
            "offset": 0,
            "attributesToRetrieve": self._retrieve_attrs(index_name, ""),
        }
        if filters:
            options["filter"] = filters
        return self.client.index(index_name).search("", options)

    def _collect_condition(
        self,
        index_name: str,
        attr: str,
        value: str,
        mode: str,
        filters: str | None,
        limit: int | None = None,
    ) -> dict[str, tuple[int, dict]]:
        result = self._search_field(
            index_name, attr, value, filters,
            limit if limit is not None else self._candidate_window(index_name, attr),
        )
        candidates: dict[str, tuple[int, dict]] = {}
        for hit in result.get("hits", []):
            text = hit.get(attr) or ""
            score = self._condition_score(
                value,
                text,
                mode,
                hit.get("_rankingScore", 0),
            )
            if score > 0:
                candidates[str(hit.get("id"))] = (score, hit)
        return candidates

    @classmethod
    def _snippet(
        cls,
        text: str,
        values: list[str],
        lead: int | None = None,
        trail: int | None = None,
    ) -> str:
        """The excerpt served for one hit: a window anchored at the match.

        ``lead`` characters of context are kept before the first occurrence of
        any of ``values``, then up to ``trail`` after it, with a ``...`` marker on
        whichever side was cut.  Anchoring is what keeps the searched word on
        screen at every viewport width (see ``SNIPPET_LEAD_CHARS``); a value that
        is not in the text at all falls back to the first matching character and
        finally to the head of the text.
        """
        lead = cls.SNIPPET_LEAD_CHARS if lead is None else lead
        trail = cls.SNIPPET_TAIL_CHARS if trail is None else trail
        clean = re.sub(r"\s+", " ", text or "").strip()
        if not clean:
            return ""

        def window(start: int, end: int) -> str:
            start = max(0, start)
            end = min(len(clean), end)
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(clean) else ""
            return f"{prefix}{clean[start:end]}{suffix}"

        lowered = clean.lower()
        for value in values:
            needle = value.strip().lower()
            if not needle:
                continue
            idx = lowered.find(needle)
            if idx >= 0:
                return window(idx - lead, idx + len(needle) + trail)
        for value in values:
            for ch in value:
                if not ch.isspace():
                    idx = lowered.find(ch.lower())
                    if idx >= 0:
                        return window(idx - lead, idx + 1 + trail)
        return clean[: lead + trail]

    def _build_chapter_entities(
        self,
        active: list[dict],
        chapter_cond_maps: dict[int, dict[str, tuple[int, dict]]],
        match: str,
    ) -> dict[str, dict]:
        all_ids: set[str] = set()
        for mapping in chapter_cond_maps.values():
            all_ids.update(mapping.keys())
        entities: dict[str, dict] = {}
        for chapter_id in all_ids:
            doc = None
            scores: dict[int, int] = {}
            for i in range(len(active)):
                entry = chapter_cond_maps.get(i, {}).get(chapter_id)
                if entry is not None:
                    scores[i] = entry[0]
                    doc = doc or entry[1]
                else:
                    scores[i] = 0
            if doc is None:
                continue
            if match == "and" and not all(scores[i] > 0 for i in range(len(active))):
                continue
            if match == "or" and not any(scores[i] > 0 for i in range(len(active))):
                continue
            entities[chapter_id] = {
                "kind": "chapter",
                "doc": doc,
                "score": sum(scores.values()),
                "scores": scores,
            }
        return entities

    def _build_book_entities(
        self,
        active: list[dict],
        book_cond_maps: dict[int, dict[str, tuple[int, dict]]],
        chapter_cond_maps: dict[int, dict[str, tuple[int, dict]]],
        match: str,
    ) -> dict[str, dict]:
        chapter_field_indices = [
            i for i, cond in enumerate(active) if cond["field"] in self.CHAPTER_FIELD_ATTRS
        ]
        book_ids: set[str] = set()
        for mapping in book_cond_maps.values():
            book_ids.update(mapping.keys())

        chapters_by_book: dict[str, set[str]] = {}
        chapter_docs: dict[str, dict] = {}
        for i in chapter_field_indices:
            mapping = chapter_cond_maps.get(i, {})
            for chapter_id, (_, doc) in mapping.items():
                book_id = str(doc.get("book_id") or "")
                if not book_id:
                    continue
                book_ids.add(book_id)
                chapters_by_book.setdefault(book_id, set()).add(chapter_id)
                chapter_docs[chapter_id] = doc

        entities: dict[str, dict] = {}
        for book_id in book_ids:
            direct_scores: dict[int, int] = {}
            for i in range(len(active)):
                entry = book_cond_maps.get(i, {}).get(book_id)
                direct_scores[i] = entry[0] if entry is not None else 0

            per_cond_max = {i: 0 for i in chapter_field_indices}
            and_chapter_totals: list[int] = []
            best_chapter_id: str | None = None
            best_chapter_score = 0
            for chapter_id in chapters_by_book.get(book_id, set()):
                row: dict[int, int] = {}
                for i in chapter_field_indices:
                    entry = chapter_cond_maps.get(i, {}).get(chapter_id)
                    row[i] = entry[0] if entry is not None else 0
                    per_cond_max[i] = max(per_cond_max[i], row[i])
                if match == "and":
                    if all(row[i] > 0 for i in chapter_field_indices):
                        chapter_total = sum(row[i] for i in chapter_field_indices)
                        and_chapter_totals.append(chapter_total)
                        if chapter_total > best_chapter_score:
                            best_chapter_score = chapter_total
                            best_chapter_id = chapter_id
                else:
                    chapter_total = sum(v for v in row.values() if v > 0)
                    and_chapter_totals.append(chapter_total)
                    if chapter_total > best_chapter_score:
                        best_chapter_score = chapter_total
                        best_chapter_id = chapter_id

            if match == "and":
                book_level_ok = all(
                    direct_scores[i] > 0 for i in book_cond_maps
                )
                chapter_level_ok = (
                    not chapter_field_indices
                    or any(total > 0 for total in and_chapter_totals)
                )
                if not (book_level_ok and chapter_level_ok):
                    continue
                chapter_score = max(and_chapter_totals) if and_chapter_totals else 0
                total = sum(direct_scores.values()) + chapter_score
            else:
                positives = [score for score in direct_scores.values() if score > 0]
                positives.extend(
                    per_cond_max[i] for i in chapter_field_indices if per_cond_max[i] > 0
                )
                if not positives:
                    continue
                total = sum(positives)

            entry = next(
                (
                    book_cond_maps[i].get(book_id)
                    for i in book_cond_maps
                    if book_id in book_cond_maps[i]
                ),
                None,
            )
            if entry is not None:
                doc = entry[1]
            else:
                first_chapter = next(iter(chapters_by_book.get(book_id, set())), None)
                chapter_doc = chapter_docs.get(first_chapter) or {}
                doc = {
                    "id": book_id,
                    "title": chapter_doc.get("book_title") or "",
                    "author": chapter_doc.get("book_author") or "",
                    "description": chapter_doc.get("book_description") or "",
                    "status": "",
                    "tags": chapter_doc.get("tags") or [],
                    "is_r18": bool(chapter_doc.get("is_r18", False)),
                }
            entities[book_id] = {
                "kind": "book",
                "doc": doc,
                "score": total,
                "scores": direct_scores,
                "matched_chapter": (
                    chapter_docs.get(best_chapter_id) if best_chapter_id else None
                ),
            }
        return entities

    def _book_matched_fields(
        self,
        book_id: str,
        active: list[dict],
        book_cond_maps: dict[int, dict[str, tuple[int, dict]]],
        chapter_cond_maps: dict[int, dict[str, tuple[int, dict]]],
    ) -> list[str]:
        matched: list[str] = []
        for i, cond in enumerate(active):
            entry = book_cond_maps.get(i, {}).get(book_id)
            if entry is not None and entry[0] > 0:
                matched.append(cond["field"])
            if cond["field"] in self.CHAPTER_FIELD_ATTRS:
                for chapter_id, (score, doc) in chapter_cond_maps.get(i, {}).items():
                    if score > 0 and str(doc.get("book_id")) == book_id:
                        matched.append(cond["field"])
                        break
        return matched

    def _serialize_book(
        self,
        entity: dict,
        active: list[dict],
        book_cond_maps: dict[int, dict[str, tuple[int, dict]]],
        chapter_cond_maps: dict[int, dict[str, tuple[int, dict]]],
    ) -> dict:
        doc = entity["doc"]
        book_id = str(doc.get("id") or "")
        description = doc.get("description") or ""
        values = [cond["value"] for cond in active]
        matched_fields = self._book_matched_fields(
            book_id,
            active,
            book_cond_maps,
            chapter_cond_maps,
        )
        matched_chapter = entity.get("matched_chapter")
        matched_chapter_payload = None
        if matched_chapter:
            chapter_content = matched_chapter.get("content") or ""
            matched_chapter_payload = {
                "id": str(matched_chapter.get("id") or ""),
                "book_id": str(matched_chapter.get("book_id") or book_id),
                "title": matched_chapter.get("title") or "",
                "chapter_number": matched_chapter.get("chapter_number"),
                "content": chapter_content[:500],
                "snippet": self._snippet(
                    chapter_content or matched_chapter.get("title") or "",
                    values,
                ),
            }
        return {
            "type": "book",
            "id": book_id,
            "book_id": book_id,
            "title": doc.get("title") or "",
            "author": doc.get("author") or "",
            "description": description[: self.DESCRIPTION_INDEX_LIMIT],
            "tags": doc.get("tags") or [],
            "category_names": doc.get("category_names") or [],
            "status": doc.get("status") or "",
            "is_r18": bool(doc.get("is_r18", False)),
            "score": entity["score"],
            "matched_fields": matched_fields,
            "snippet": (
                self._snippet(description or doc.get("title") or "", values)
                if any(field in ("description", "content") for field in matched_fields)
                else ""
            ),
            "matched_chapter": matched_chapter_payload,
        }

    def _serialize_chapter(self, entity: dict, active: list[dict]) -> dict:
        doc = entity["doc"]
        content = doc.get("content") or ""
        values = [cond["value"] for cond in active]
        return {
            "type": "chapter",
            "id": str(doc.get("id") or ""),
            "chapter_id": str(doc.get("id") or ""),
            "book_id": str(doc.get("book_id") or ""),
            "title": doc.get("title") or "",
            "book_title": doc.get("book_title") or "",
            "author": doc.get("book_author") or "",
            "category_names": doc.get("category_names") or [],
            "chapter_number": doc.get("chapter_number"),
            "content": content[:500],
            "snippet": self._snippet(content or doc.get("title") or "", values),
            "score": entity["score"],
            "matched_fields": [
                active[i]["field"]
                for i, score in entity["scores"].items()
                if score > 0
            ],
        }

    # ---- single-condition search: one scan, then cached deep pages ----

    @staticmethod
    def _scope_index(scope: str) -> str:
        return (
            SearchService.INDEX_BOOKS if scope == "books"
            else SearchService.INDEX_CHAPTERS
        )

    def _field_attr(self, index_name: str, field: str) -> str | None:
        """Which indexed attribute a condition field maps to for one index."""
        if index_name == self.INDEX_BOOKS:
            return self.BOOK_FIELD_ATTRS.get(field)
        if field in self.CHAPTER_FIELD_ATTRS:
            return self.CHAPTER_FIELD_ATTRS[field]
        return self.CHAPTER_BOOK_FIELD_ATTRS.get(field)

    def _content_window_for(self, offset: int) -> int:
        """How far a 正文 search has to scan to serve ``offset``.

        Grows in ``CONTENT_CANDIDATE_LIMIT`` steps so that page 9 costs the same
        per candidate as page 1, and caps at ``CONTENT_WINDOW_MAX``.  Metadata
        keeps the flat 10 000 window: its scan does not carry any text, so page
        250 costs nothing extra.
        """
        needed = max(self.CONTENT_CANDIDATE_LIMIT, int(offset) + 1)
        steps = -(-needed // self.CONTENT_CANDIDATE_LIMIT)  # ceil
        return min(self.CONTENT_WINDOW_MAX, steps * self.CONTENT_CANDIDATE_LIMIT)

    def _scan_condition(
        self,
        index_name: str,
        attr: str,
        value: str,
        filters: str | None,
        window: int,
        mode: str = "exact",
        chunk_size: int | None = None,
    ) -> list[tuple[int, str, str]]:
        """Rank the engine's candidates with the shared Python scorer.

        Only ``id``, the searched field and the cheapest display fields are
        retrieved, so a 10 000 document window costs 0.02-0.7 s instead of the
        96 MB / 18 s that pulling every chapter body used to cost.  The result is
        a ranked ``(score, id, snippet)`` list -- tiny enough to cache, which is
        what makes pages 2..N cheap.

        A window of at most ``chunk_size`` candidates is still exactly one
        request -- ``window`` itself for a metadata scan, ``_SCAN_CHUNK`` for a
        正文 one.  A wider window (only a deep 正文 page reaches it) is walked in
        chunks, because Meilisearch will not build a 2 000-document body-carrying
        page in one answer; a short chunk also means the engine has nothing left,
        so the rest of the window is skipped.

        The snippet is built here because this is the only place that holds the
        stored text of every hit.  The page that is served is hydrated with an
        *empty* query (``_hydrate``), and Meilisearch only crops around the query
        terms, so the excerpt it returns for a 正文 search is the head of the
        chapter -- the searched word is usually absent from it.  ``attr`` is only
        the body for a 正文 condition; metadata fields keep ``""`` so the page
        cache stays dominated by the ids.
        """
        retrieve = ["id", attr]
        if index_name == self.INDEX_BOOKS:
            retrieve.append("author")
        else:
            retrieve.extend(["book_id", "book_title"])
        hits: list[dict] = []
        fetched = 0
        step = self._SCAN_CHUNK if chunk_size is None else max(1, int(chunk_size))
        while fetched < window:
            chunk = min(step, window - fetched)
            options = {
                "limit": chunk,
                "offset": fetched,
                "attributesToSearchOn": [attr],
                "attributesToRetrieve": list(dict.fromkeys(retrieve)),
                "showRankingScore": True,
            }
            if filters:
                options["filter"] = filters
            page = self.client.index(index_name).search(value, options)
            page_hits = page.get("hits", []) or []
            hits.extend(page_hits)
            fetched += len(page_hits)
            if len(page_hits) < chunk:
                break
        scored: list[tuple[int, str, str]] = []
        for hit in hits:
            text = hit.get(attr) or ""
            score = self._condition_score(
                value,
                text,
                mode,
                hit.get("_rankingScore", 0),
            )
            if score > 0:
                scored.append((
                    score,
                    str(hit.get("id")),
                    self._snippet(text, [value]) if attr == "content" else "",
                ))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return scored

    def _hydrate(self, index_name: str, ids: list[str]) -> dict[str, dict]:
        """Fetch the full documents for the ids of one page.

        The ranked list only carries ``(score, id)``, so the page that is
        actually returned gets its display fields (and a cropped body) from one
        small filtered query.  This needs ``id`` in the index's filterable
        attributes, which ``_ensure_index`` sets; Meilisearch applies settings
        asynchronously, so a query racing the very first startup may still be
        rejected -- the caller then falls back to the scanned fields instead of
        failing the whole search.
        """
        if not ids:
            return {}
        quoted = ", ".join(json.dumps(str(doc_id)) for doc_id in ids)
        options = {
            "limit": len(ids),
            "filter": f"id IN [{quoted}]",
            "attributesToRetrieve": self._retrieve_attrs(index_name, ""),
        }
        if index_name == self.INDEX_CHAPTERS:
            options["attributesToCrop"] = ["content"]
            options["cropLength"] = self.SNIPPET_CROP_WORDS
        try:
            result = self.client.index(index_name).search("", options)
        except meilisearch.errors.MeilisearchApiError as exc:
            logger.warning("Search page hydration failed: {}", exc)
            return {}
        return {str(hit.get("id")): hit for hit in result.get("hits", []) or []}

    def _hydrate_around(
        self,
        index_name: str,
        ids: list[str],
        query: str,
        attr: str,
    ) -> dict[str, dict]:
        """Hydrate one page with its body cropped around the match.

        ``_hydrate`` asks with an empty query, so the crop it gets is the head of
        the chapter; sending the conjunction terms back with the ``id IN`` filter
        centres the excerpt on the match instead (0.3 s for 40 chapters on the
        live index), which is what the result list shows.
        """
        if not ids:
            return {}
        quoted = ", ".join(json.dumps(str(doc_id)) for doc_id in ids)
        options = {
            "limit": len(ids),
            "filter": f"id IN [{quoted}]",
            "attributesToRetrieve": self._retrieve_attrs(index_name, ""),
            "attributesToCrop": [attr],
            "cropLength": self.SNIPPET_CROP_WORDS,
        }
        if index_name == self.INDEX_CHAPTERS and "content" not in options["attributesToCrop"]:
            options["attributesToCrop"].append("content")
        try:
            result = self.client.index(index_name).search(query, options)
        except meilisearch.errors.MeilisearchApiError as exc:
            logger.warning("Search page hydration failed: {}", exc)
            return {}
        return {str(hit.get("id")): hit for hit in result.get("hits", []) or []}

    @staticmethod
    def _cropped_content(hit: dict) -> str:
        formatted = hit.get("_formatted") or {}
        return str(formatted.get("content") or "")

    def _serialize_engine_hit(
        self,
        hit: dict,
        field: str,
        scope: str,
        snippet: str = "",
    ) -> dict:
        score = int(max(0.0, min(float(hit.get("_rankingScore") or 0.0), 1.0)) * 1000)
        if scope == "books":
            description = hit.get("description") or ""
            return {
                "type": "book",
                "id": str(hit.get("id") or ""),
                "book_id": str(hit.get("id") or ""),
                "title": hit.get("title") or "",
                "author": hit.get("author") or "",
                "description": description[: self.DESCRIPTION_INDEX_LIMIT],
                "tags": hit.get("tags") or [],
                "category_names": hit.get("category_names") or [],
                "status": hit.get("status") or "",
                "is_r18": bool(hit.get("is_r18", False)),
                "score": score,
                "matched_fields": [field],
                "snippet": (
                    self._snippet(description, []) if field == "description" else ""
                ),
                "matched_chapter": None,
            }
        # ``snippet`` is the anchored excerpt a 正文 scan already built; the
        # engine crop is only a fallback for the searches that do not scan the
        # body (metadata conditions) or for a page hydrated without one.
        content = snippet or self._cropped_content(hit)
        return {
            "type": "chapter",
            "id": str(hit.get("id") or ""),
            "chapter_id": str(hit.get("id") or ""),
            "book_id": str(hit.get("book_id") or ""),
            "title": hit.get("title") or "",
            "book_title": hit.get("book_title") or "",
            "author": hit.get("book_author") or "",
            "category_names": hit.get("category_names") or [],
            "chapter_number": hit.get("chapter_number"),
            "content": content[:500],
            "snippet": content,
            "score": score,
            "matched_fields": [field],
        }

    def _single_condition_search(
        self,
        cond: dict,
        *,
        scope: str,
        filters: str | None,
        offset: int,
        limit: int,
    ) -> dict | None:
        """Deep-pageable search for one condition, or ``None`` to use the window.

        Both modes scan the engine's candidates once, score them in Python, cache
        the ranking and hydrate only the page that is returned:

        * ``exact`` -- the bare substring test Meilisearch cannot express;
        * ``fuzzy`` -- every character of the query must appear.  This used to be
          handed straight to the engine, but Meilisearch's CJK matching is
          per-character, so the noise the user sees (铃铛 -> 铃木/铃雨) *is* its
          answer.  Scoring here keeps the gate and the honest ``total``.
        """
        index_name = self._scope_index(scope)
        attr = self._field_attr(index_name, cond["field"])
        if not attr:
            return None

        window = (
            self._content_window_for(offset) if attr == "content"
            else self.METADATA_CANDIDATE_LIMIT
        )
        # Metadata scans carry no text, so one wide request per page is fine (the
        # 10 000 window is ~10 MB and has always been fetched like that).  A 正文
        # scan drags the chapter bodies along, so it is fetched in 1 000-row
        # pieces -- exactly one request for every window up to the first eight
        # pages, and one chunk per extra 1 000 candidates beyond them.
        chunk_size = self._SCAN_CHUNK if attr == "content" else window
        cache_key = "|".join((
            index_name, attr, cond["value"], filters or "",
            cond.get("mode") or "exact", str(window),
        ))
        ranked = self._page_cache_get(cache_key)
        if ranked is None:
            ranked = self._scan_condition(
                index_name, attr, cond["value"], filters, window,
                cond.get("mode") or "exact", chunk_size=chunk_size,
            )
            self._page_cache_put(cache_key, ranked)

        page_rows = ranked[offset : offset + limit]
        docs = self._hydrate(index_name, [row[1] for row in page_rows])
        hits = []
        for score, doc_id, snippet in page_rows:
            hit = docs.get(doc_id)
            if hit is None:
                # Hydration unavailable (settings task still applying): keep the
                # row with the little we know rather than dropping the result.
                hit = {"id": doc_id}
            hits.append(
                self._serialize_engine_hit(
                    hit, cond["field"], scope, snippet=snippet,
                )
            )
        return {
            "hits": hits,
            "total": len(ranked),
            "offset": offset,
            "limit": limit,
        }

    def _page_cache_get(self, key: str) -> list[tuple[int, str, str]] | None:
        entry = self._page_cache.get(key)
        if entry is None:
            return None
        expires_at, ranked = entry
        if expires_at < time.monotonic():
            self._page_cache.pop(key, None)
            return None
        return ranked

    def _page_cache_put(self, key: str, ranked: list[tuple[int, str, str]]) -> None:
        now = time.monotonic()
        for stale in [
            cache_key
            for cache_key, (expires_at, _) in self._page_cache.items()
            if expires_at < now
        ]:
            self._page_cache.pop(stale, None)
        self._page_cache[key] = (now + self.PAGE_CACHE_TTL_SECONDS, ranked)
        while len(self._page_cache) > self.PAGE_CACHE_MAX_ENTRIES:
            oldest = min(
                self._page_cache.items(), key=lambda item: item[1][0],
            )[0]
            self._page_cache.pop(oldest, None)

    # ---- multi-condition AND: one conjunction query, then the same scorer ----

    def _same_field_conjunction(
        self,
        active: list[dict],
        match: str,
    ) -> tuple[str, str] | None:
        """``(index, attribute)`` when every condition ANDs one single field.

        That is the shape Meilisearch can answer natively: it returns the
        documents carrying *all* the query's terms instead of two detached
        relevance windows whose intersection is usually empty.
        """
        if match != "and" or len(active) < 2:
            return None
        target: tuple[str, str] | None = None
        for cond in active:
            for index_name in (self.INDEX_CHAPTERS, self.INDEX_BOOKS):
                attr = self._field_attr(index_name, cond["field"])
                if not attr:
                    continue
                candidate = (index_name, attr)
                if target is None:
                    target = candidate
                elif target != candidate:
                    return None
                break
            else:  # pragma: no cover - fields are validated before we get here
                return None
        return target

    @staticmethod
    def _conjunction_query(active: list[dict]) -> str:
        """The engine query for an AND: one term per condition, deduplicated.

        Exact multi-character values are sent as ``"phrase"`` queries.
        Meilisearch tokenizes CJK into single characters, so a bare
        ``师妹 乳环`` with ``matchingStrategy: "all"`` matches any chapter
        holding the four characters scattered anywhere (3 094 hits on the live
        index for a 20-chapter intersection), while the page-level substring
        gate then drops every one of them -- total says thousands, the list is
        empty.  Quoting makes the engine require the contiguous substring,
        which is exactly what exact mode verifies.  Fuzzy values stay
        unquoted: fuzzy only requires every character to appear, which is what
        the bare character tokens already express.
        """
        terms: list[str] = []
        seen: set[str] = set()
        for cond in active:
            value = str(cond.get("value") or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            mode = cond.get("mode") or "exact"
            if mode == "exact" and len(value) > 1:
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                terms.append(f'"{escaped}"')
            else:
                terms.append(value)
        return " ".join(terms)

    def _conjunction_rank(
        self,
        index_name: str,
        attr: str,
        active: list[dict],
        filters: str | None,
        window: int,
    ) -> tuple[list[tuple[int, str, str]], int | None]:
        """Engine-ranked ids for ``cond1 AND cond2 AND …`` on one attribute.

        The query is the whitespace-joined values with ``matchingStrategy:
        "all"``, which makes Meilisearch return only documents that carry every
        term (measured on the live chapters index: 铃 + 仙 -> 1 299 documents,
        versus 0 through the per-condition windows).

        Only ``id`` is retrieved.  Asking for the searched attribute instead
        costs 12 s per 1 000 chapters on that index, and the *page* re-checks
        the real text anyway (``_conjunction_search``) -- the engine is only
        trusted to narrow tens of thousands of matches down to the window.

        Returns the ranked ``(score, id, "")`` list and the engine's hit count;
        the count is ``None`` when the engine does not report one.
        """
        query = self._conjunction_query(active)
        if not query:
            return [], 0
        options = {
            "limit": window,
            "offset": 0,
            "attributesToSearchOn": [attr],
            "attributesToRetrieve": ["id"],
            "matchingStrategy": "all",
        }
        if filters:
            options["filter"] = filters
        index = self.client.index(index_name)
        try:
            result = index.search(query, options)
        except meilisearch.errors.MeilisearchApiError as exc:
            # ``matchingStrategy`` needs Meilisearch >= 1.3; an older engine (or
            # an attribute that is not searchable yet) must not turn a search
            # into a hard failure.
            logger.warning("Conjunction search fell back for {}: {}", attr, exc)
            options.pop("matchingStrategy", None)
            options.pop("attributesToSearchOn", None)
            result = index.search(query, options)
        # Every hit passed the engine's "all terms" gate, so they all satisfy
        # the conjunction; the page-level gate decides what is displayed.  The
        # snippet slot stays empty: the page is hydrated with its body cropped
        # around the match instead (``_serialize_scored_hit``).
        ranked = [
            (len(active), str(hit.get("id")), "")
            for hit in result.get("hits", []) or []
        ]
        total = result.get("estimatedTotalHits")
        return ranked, (int(total) if isinstance(total, int) else None)

    def _conjunction_values(
        self,
        index_name: str,
        ids: list[str],
        attr: str,
    ) -> dict[str, str]:
        """The stored text of ``attr`` for a page's ids, in one engine call."""
        if not ids or not attr:
            return {}
        quoted = ", ".join(json.dumps(str(doc_id)) for doc_id in ids)
        try:
            result = self.client.index(index_name).search("", {
                "limit": len(ids),
                "filter": f"id IN [{quoted}]",
                "attributesToRetrieve": ["id", attr],
            })
        except meilisearch.errors.MeilisearchApiError as exc:
            logger.warning("Conjunction verification unavailable: {}", exc)
            return {}
        values: dict[str, str] = {}
        for hit in result.get("hits", []) or []:
            text = hit.get(attr)
            # No stored value means the field is not retrievable; report it as
            # "unknown" so the caller falls back to the engine's own match.
            values[str(hit.get("id"))] = "" if text is None else str(text)
        return values

    def _conjunction_search(
        self,
        index_name: str,
        attr: str,
        active: list[dict],
        *,
        filters: str | None,
        offset: int,
        limit: int,
        scope: str,
    ) -> dict | None:
        """Answer a same-field AND from one conjunction query, with deep paging.

        Only chapter fields take this path.  Book metadata ANDs are already
        complete: the 10 000-candidate books window reaches every match, and the
        engine cannot express ``category = 言情`` as a query term anyway.
        """
        if attr not in self.CHAPTER_FIELD_ATTRS.values():
            return None
        cache_key = "|".join((
            "conj", index_name, attr, self._conjunction_query(active),
            filters or "",
            ",".join(cond.get("mode") or "exact" for cond in active),
            str(self.CONJUNCTION_CANDIDATE_LIMIT),
        ))
        ranked = self._page_cache_get(cache_key)
        engine_total: int | None = None
        now = time.monotonic()
        cached_total = self._conjunction_totals.get(cache_key)
        if cached_total is not None and cached_total[0] >= now:
            engine_total = cached_total[1]
        if ranked is None:
            ranked, engine_total = self._conjunction_rank(
                index_name, attr, active, filters,
                self.CONJUNCTION_CANDIDATE_LIMIT,
            )
            self._page_cache_put(cache_key, ranked)
            self._conjunction_totals[cache_key] = (
                now + self.PAGE_CACHE_TTL_SECONDS, engine_total,
            )
            for stale in [
                key for key, (expires_at, _) in self._conjunction_totals.items()
                if expires_at < now
            ]:
                self._conjunction_totals.pop(stale, None)
        page_rows = ranked[offset : offset + limit]
        docs = self._hydrate_around(
            index_name,
            [row[1] for row in page_rows],
            self._conjunction_query(active),
            attr,
        )
        page_ids = [row[1] for row in page_rows]
        # Re-check the page against the stored text.  Quoted exact values make
        # the engine require the contiguous substring, so a mismatch here means
        # a fuzzy condition scattered its characters -- the only residual gap
        # the engine can still produce.  Verified survivors are always served;
        # unverifiable ids are served too (the engine required every quoted
        # term), so the list can never be shorter than what this page ranked.
        # The honest ``total`` is what the engine counted; a page that verifies
        # to fewer rows still keeps that total, otherwise the header count
        # ("一千多条") would contradict an empty list ("没有找到结果").
        stored: dict[str, str] = {}
        if page_ids and len(page_ids) <= self.CONJUNCTION_VERIFY_MAX_HITS:
            stored = self._conjunction_values(index_name, page_ids, attr)
        hits = []
        for _score, doc_id, _snippet in page_rows:
            hit = docs.get(doc_id) or {"id": doc_id}
            text = stored.get(doc_id)
            if text is None:
                hits.append(self._serialize_engine_hit(hit, active[0]["field"], scope))
                continue
            score = sum(
                1
                for cond in active
                if self._condition_score(
                    cond["value"], text, cond.get("mode") or "exact",
                ) > 0
            )
            if score < len(active):
                # AND gate failed: only a fuzzy condition can still fail here.
                # Exact values were already required as contiguous phrases by
                # the quoted engine query, so dropping the row would make the
                # list shorter than the ranked page (and, at the extreme, an
                # empty list under a non-zero total).  Fuzzy values keep their
                # bare character tokens, so their scatter matches must still
                # be dropped -- that is the one residual gap the engine leaves.
                has_fuzzy = any(
                    (cond.get("mode") or "exact") != "exact"
                    for cond in active
                )
                if not has_fuzzy:
                    score = len(active)
                else:
                    continue
            hits.append(
                self._serialize_scored_hit(
                    hit,
                    score,
                    [cond["field"] for cond in active],
                    scope,
                    values=[cond["value"] for cond in active],
                )
            )
        # The engine's count is capped by the cached ranking: it may count
        # 1 299 hits while the window kept the best 1 000, and promising pages
        # the window cannot serve is worse than a slightly smaller total.
        # ``MAX_TOTAL_HITS`` is Meilisearch's own paging ceiling.
        total = min(
            engine_total if engine_total is not None else len(ranked),
            max(len(ranked), self.MAX_TOTAL_HITS),
        )
        total = max(total, offset + len(page_rows))
        return {"hits": hits, "total": total, "offset": offset, "limit": limit}

    def _serialize_scored_hit(
        self,
        hit: dict,
        score: int,
        fields: list[str],
        scope: str,
        values: list[str] | None = None,
    ) -> dict:
        """A hydrated page hit carrying the Python score of a conjunction."""
        if scope == "books":
            payload = self._serialize_engine_hit(hit, fields[0], "books")
            payload["score"] = score
            payload["matched_fields"] = fields
            return payload
        payload = self._serialize_engine_hit(hit, fields[0], "chapters")
        payload["score"] = score
        payload["matched_fields"] = fields
        # ``_hydrate_around`` crops the body around the match, so the served
        # snippet is the engine's own excerpt re-anchored at the hit (a centred
        # crop puts the searched word around the middle of the window, which the
        # two-line clamp hides on a phone); the raw ``content`` stays empty here
        # because the full body is not retrieved for a page.
        snippet = self._snippet(self._cropped_content(hit), values or [])
        if snippet:
            payload["snippet"] = snippet
        return payload

    def advanced_search(
        self,
        conditions: list[dict],
        *,
        match: str = "and",
        scope: str = "all",
        tag: str | None = None,
        source_id: str | None = None,
        kind: str | None = None,
        offset: int = 0,
        limit: int = 20,
        allow_r18: bool = True,
        allow_all_ages: bool = True,
    ) -> dict:
        self._ensure_index(self.INDEX_BOOKS)
        self._ensure_index(self.INDEX_CHAPTERS)
        active: list[dict] = []
        valid_fields = set(self.BOOK_FIELD_ATTRS) | set(self.CHAPTER_FIELD_ATTRS)
        for cond in conditions:
            field = cond.get("field") or ""
            mode = cond.get("mode") or "exact"
            value = (cond.get("value") or "").strip()
            if value and field in valid_fields and mode in ("exact", "fuzzy"):
                active.append({"field": field, "mode": mode, "value": value})
        if not active:
            if tag:
                filters = self._combined_filter(
                    allow_r18, allow_all_ages, tag, source_id, kind,
                )
                if scope in ("all", "books"):
                    result = self._search_all_with_filter(self.INDEX_BOOKS, filters)
                    hits = [
                        self._serialize_book(
                            {"kind": "book", "doc": hit, "score": 0, "scores": {}},
                            [],
                            {},
                            {},
                        )
                        for hit in result.get("hits", [])
                    ]
                    hits.sort(key=lambda hit: hit["title"].lower())
                    return {
                        "hits": hits[offset : offset + limit],
                        "total": len(hits),
                        "offset": offset,
                        "limit": limit,
                    }
                if scope == "chapters":
                    result = self._search_all_with_filter(self.INDEX_CHAPTERS, filters)
                    hits = [
                        self._serialize_chapter(
                            {"kind": "chapter", "doc": hit, "score": 0, "scores": {}},
                            [],
                        )
                        for hit in result.get("hits", [])
                    ]
                    hits.sort(key=lambda hit: hit["title"].lower())
                    return {
                        "hits": hits[offset : offset + limit],
                        "total": len(hits),
                        "offset": offset,
                        "limit": limit,
                    }
            return {"hits": [], "total": 0, "offset": offset, "limit": limit}

        effective_scope = scope
        if scope == "all":
            # Metadata searches (title/author/tags/description/category) should
            # return books only; chapter-title/content searches should return
            # chapters only. This avoids showing every chapter for an author or
            # tag query, and avoids duplicate book+chapter results for content.
            effective_scope = (
                "chapters"
                if any(cond["field"] in self.CHAPTER_FIELD_ATTRS for cond in active)
                else "books"
            )

        filters = self._combined_filter(
            allow_r18, allow_all_ages, tag, source_id, kind,
        )
        # Several conditions on one chapter field (the 正文 「铃」 AND 正文 「仙」
        # case) are answered by a single conjunction query: intersecting two
        # detached relevance windows is arithmetically hopeless -- both fields
        # match tens of thousands of chapters, so their top-300 candidates
        # almost never overlap and the result was 0.  The engine can express
        # that AND, and its ranking is what makes paging consistent.
        if effective_scope in ("books", "chapters"):
            conjunction = self._same_field_conjunction(active, match)
            if conjunction is not None:
                conjunction_result = self._conjunction_search(
                    conjunction[0],
                    conjunction[1],
                    active,
                    filters=filters,
                    offset=offset,
                    limit=limit,
                    scope=effective_scope,
                )
                if conjunction_result is not None:
                    return conjunction_result

        # A single condition is the overwhelmingly common case (the quick search
        # box) and the only one that can be answered with deep paging.  Two or
        # more conditions span fields/AND-OR combinations that Meilisearch cannot
        # express as one query, so they keep the bounded candidate window below.
        if len(active) == 1 and effective_scope in ("books", "chapters"):
            single = self._single_condition_search(
                active[0],
                scope=effective_scope,
                filters=filters,
                offset=offset,
                limit=limit,
            )
            if single is not None:
                return single

        book_cond_maps: dict[int, dict[str, tuple[int, dict]]] = {}
        chapter_cond_maps: dict[int, dict[str, tuple[int, dict]]] = {}
        has_chapter_fields = any(
            cond["field"] in self.CHAPTER_FIELD_ATTRS for cond in active
        )
        for i, cond in enumerate(active):
            field = cond["field"]
            if field in self.BOOK_FIELD_ATTRS:
                if effective_scope in ("all", "books"):
                    book_cond_maps[i] = self._collect_condition(
                        self.INDEX_BOOKS,
                        self.BOOK_FIELD_ATTRS[field],
                        cond["value"],
                        cond["mode"],
                        filters,
                    )
                # A book-scoped query with a chapter condition still needs
                # chapter metadata to aggregate the chapter match into a book.
                if effective_scope in ("chapters", "all") or (
                    effective_scope == "books" and has_chapter_fields
                ):
                    chapter_cond_maps[i] = self._collect_condition(
                        self.INDEX_CHAPTERS,
                        self.CHAPTER_BOOK_FIELD_ATTRS[field],
                        cond["value"],
                        cond["mode"],
                        filters,
                    )
            elif effective_scope in ("books", "chapters", "all"):
                chapter_cond_maps[i] = self._collect_condition(
                    self.INDEX_CHAPTERS,
                    self.CHAPTER_FIELD_ATTRS[field],
                    cond["value"],
                    cond["mode"],
                    filters,
                )

        entities: list[dict] = []
        if effective_scope in ("all", "books"):
            entities.extend(
                self._build_book_entities(
                    active,
                    book_cond_maps,
                    chapter_cond_maps,
                    match,
                ).values()
            )
        if effective_scope in ("all", "chapters"):
            entities.extend(
                self._build_chapter_entities(active, chapter_cond_maps, match).values()
            )

        entities.sort(
            key=lambda entity: (
                -entity["score"],
                (entity["doc"].get("title") or "").lower(),
            )
        )
        total = len(entities)
        page = entities[offset : offset + limit]
        hits = []
        for entity in page:
            if entity["kind"] == "book":
                hits.append(
                    self._serialize_book(
                        entity,
                        active,
                        book_cond_maps,
                        chapter_cond_maps,
                    )
                )
            else:
                hits.append(self._serialize_chapter(entity, active))
        return {"hits": hits, "total": total, "offset": offset, "limit": limit}

    def delete_book(self, book_id: str) -> None:
        try:
            self.client.index(self.INDEX_BOOKS).delete_document(book_id)
            logger.debug("Deleted book {} from search index", book_id)
        except meilisearch.errors.MeilisearchApiError:
            pass

    def delete_chapter(self, chapter_id: str) -> None:
        try:
            self.client.index(self.INDEX_CHAPTERS).delete_document(chapter_id)
            logger.debug("Deleted chapter {} from search index", chapter_id)
        except meilisearch.errors.MeilisearchApiError:
            pass

    def delete_chapter_from_index(self, chapter_id: str) -> None:
        try:
            self.client.index(self.INDEX_CHAPTERS).delete_document(chapter_id)
            logger.debug("Deleted chapter {} from search index", chapter_id)
        except Exception:
            pass

    def delete_chapters_from_index(self, chapter_ids: list[str]) -> None:
        """Delete chapter documents in batches.

        Deleting a whole library one document at a time makes the API request
        block for thousands of HTTP round trips. Meilisearch accepts a list,
        which turns a library cleanup into a handful of requests.
        """
        ids = [str(i) for i in chapter_ids if i]
        if not ids:
            return
        index = self.client.index(self.INDEX_CHAPTERS)
        for start in range(0, len(ids), 1000):
            try:
                index.delete_documents(ids[start : start + 1000])
            except Exception as exc:
                logger.warning(
                    "Failed to delete {} chapters from search index: {}",
                    len(ids[start : start + 1000]),
                    exc,
                )
                return

    def delete_books_from_index(self, book_ids: list[str]) -> None:
        """Delete book documents in batches."""
        ids = [str(i) for i in book_ids if i]
        if not ids:
            return
        index = self.client.index(self.INDEX_BOOKS)
        for start in range(0, len(ids), 1000):
            try:
                index.delete_documents(ids[start : start + 1000])
            except Exception as exc:
                logger.warning(
                    "Failed to delete {} books from search index: {}",
                    len(ids[start : start + 1000]),
                    exc,
                )
                return

    def get_index_stats(self) -> dict:
        """Return document counts for both indexes."""
        try:
            self.ensure_indexes()
        except Exception as exc:
            logger.warning("Failed to ensure search indexes before stats: {}", exc)
        result = {}
        for idx_name in [self.INDEX_BOOKS, self.INDEX_CHAPTERS]:
            try:
                idx = self.client.get_index(idx_name)
                stats = idx.get_stats()
                result[idx_name] = {
                    "documents": stats.number_of_documents,
                    "is_indexing": stats.is_indexing,
                    "last_update": getattr(stats, "updated_at", None),
                }
            except Exception:
                result[idx_name] = {"documents": 0, "is_indexing": False, "last_update": None}
        return result

    async def rebuild_index(self) -> dict:
        """Rebuild both indexes from database and storage records."""
        result = {"books": 0, "chapters": 0}

        async def _rebuild():
            from app.core.database import SessionLocal
            from app.models import Book, BookCustomTag, Chapter, CustomTag
            from app.services.storage import BookStorage
            from sqlalchemy import select

            storage = BookStorage()
            async with SessionLocal() as db:
                for idx_name in [self.INDEX_BOOKS, self.INDEX_CHAPTERS]:
                    try:
                        self.client.delete_index(idx_name)
                    except Exception:
                        pass
                self._ensured.clear()
                self._ensure_index(self.INDEX_BOOKS)
                self._ensure_index(self.INDEX_CHAPTERS)

                books = (await db.scalars(select(Book))).unique().all()
                custom_rows = await db.execute(
                    select(BookCustomTag.book_id, CustomTag.name)
                    .join(CustomTag, BookCustomTag.custom_tag_id == CustomTag.id)
                )
                custom_tag_map: dict[str, list[str]] = {}
                for book_id, name in custom_rows.all():
                    custom_tag_map.setdefault(str(book_id), []).append(name)
                book_meta: dict[str, dict] = {}
                for book in books:
                    tags = list(
                        dict.fromkeys(
                            [*book.tag_names, *custom_tag_map.get(book.id, [])]
                        )
                    )
                    doc = {
                        "id": book.id,
                        "title": book.title,
                        "author": book.author_name or "",
                        "description": book.description or "",
                        "status": book.status or "",
                        "source_id": book.source_id or "",
                        "author_id": book.author_id or "",
                        "is_r18": book.is_r18,
                        "tags": tags,
                        "category_names": book.category_names,
                        "kind": normalize_kind(book.kind),
                    }
                    book_meta[book.id] = doc
                    self.index_book(doc)
                result["books"] = len(books)

                chapters = (await db.scalars(select(Chapter))).unique().all()
                batch: list[dict] = []
                for chapter in chapters:
                    meta = book_meta.get(chapter.book_id) or {}
                    content = ""
                    if chapter.content_path:
                        try:
                            content = storage.read_chapter(chapter.content_path)
                        except Exception:
                            content = ""
                    batch.append({
                        "id": chapter.id,
                        "book_id": chapter.book_id,
                        "title": chapter.title or "",
                        "chapter_number": chapter.chapter_number or 0,
                        "book_title": meta.get("title", ""),
                        "book_author": meta.get("author", ""),
                        "book_description": (meta.get("description") or "")[
                            : self.DESCRIPTION_INDEX_LIMIT
                        ],
                        "content": content[: self.CONTENT_INDEX_LIMIT],
                        "is_r18": bool(meta.get("is_r18", False)),
                        "tags": meta.get("tags") or [],
                        "category_names": meta.get("category_names") or [],
                        "kind": meta.get("kind") or KIND_NOVEL,
                    })
                    if len(batch) >= self.CHAPTER_BUFFER_SIZE:
                        self.client.index(self.INDEX_CHAPTERS).add_documents(batch)
                        result["chapters"] += len(batch)
                        batch = []
                if batch:
                    self.client.index(self.INDEX_CHAPTERS).add_documents(batch)
                    result["chapters"] += len(batch)

        await _rebuild()
        return result

    KIND_BACKFILL_BATCH = 1000

    def index_missing_kind(self, index_name: str) -> bool:
        """Whether an index still holds documents without the ``kind`` field.

        The field was added after both indexes already held the whole library,
        and a document without it matches neither ``kind = "novel"`` nor
        ``kind = "comic"`` -- so ``/novels`` and ``/comics`` search would return
        nothing until it is backfilled.
        """
        self._ensure_index(index_name)
        try:
            result = self.client.index(index_name).search("", {
                "limit": 1,
                "filter": "kind NOT EXISTS",
                "attributesToRetrieve": ["id"],
            })
        except Exception as exc:
            logger.warning("Could not probe {} for kind: {}", index_name, exc)
            # Do not trigger a full backfill on an unknown index state.
            return False
        return bool(result.get("hits"))

    async def sync_book_kinds(
        self,
        *,
        force: bool = False,
        book_ids: list[str] | None = None,
    ) -> dict:
        """Copy each book's ``kind`` onto its documents in both search indexes.

        Uses ``update_documents`` so only ``kind`` is merged into the existing
        documents.  Three callers, three shapes:

        * ``book_ids`` given (after ``/books/reclassify``) -- refresh only the
          books whose kind actually changed, plus their chapters;
        * ``force=True`` -- rewrite the whole library;
        * default (the startup path) -- skip an index whose probe finds nothing
          missing, so a normal restart never re-indexes the whole library.

        Chapters need the field too: a ``content`` search runs against the
        chapters index, so without it a search started on /novels or /comics
        would silently return nothing.
        """
        self._ensure_index(self.INDEX_BOOKS)
        self._ensure_index(self.INDEX_CHAPTERS)
        targets = None
        if book_ids is not None:
            targets = [str(book_id) for book_id in book_ids if book_id]
            if not targets:
                return {"books": 0, "chapters": 0, "skipped": True}
            need_books = need_chapters = True
        else:
            need_books = force or self.index_missing_kind(self.INDEX_BOOKS)
            need_chapters = force or self.index_missing_kind(self.INDEX_CHAPTERS)
        result = {"books": 0, "chapters": 0, "skipped": not (need_books or need_chapters)}
        if result["skipped"]:
            return result

        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models import Book, Chapter

        batches = (
            [targets[i : i + self.KIND_BACKFILL_BATCH] for i in range(0, len(targets), self.KIND_BACKFILL_BATCH)]
            if targets is not None
            else None
        )

        async with SessionLocal() as db:
            books_index = self.client.index(self.INDEX_BOOKS)
            chapters_index = self.client.index(self.INDEX_CHAPTERS)

            async def _book_docs(book_query) -> list[dict]:
                rows = (await db.execute(book_query)).all()
                return [
                    {"id": str(book_id), "kind": normalize_kind(kind)}
                    for book_id, kind in rows
                ]

            async def _chapter_docs(chapter_query) -> list[dict]:
                rows = (await db.execute(chapter_query)).all()
                return [
                    {"id": str(chapter_id), "kind": normalize_kind(kind)}
                    for chapter_id, kind in rows
                ]

            if batches is not None:
                # Targeted refresh: one round trip per batch for books and for
                # their chapters.
                for batch in batches:
                    if need_books:
                        docs = await _book_docs(
                            select(Book.id, Book.kind).where(Book.id.in_(batch))
                        )
                        if docs:
                            books_index.update_documents(docs)
                            result["books"] += len(docs)
                    if need_chapters:
                        docs = await _chapter_docs(
                            select(Chapter.id, Book.kind)
                            .join(Book, Book.id == Chapter.book_id)
                            .where(Chapter.book_id.in_(batch))
                        )
                        if docs:
                            chapters_index.update_documents(docs)
                            result["chapters"] += len(docs)
            else:
                if need_books:
                    last_id = ""
                    while True:
                        docs = await _book_docs(
                            select(Book.id, Book.kind)
                            .where(Book.id > last_id)
                            .order_by(Book.id)
                            .limit(self.KIND_BACKFILL_BATCH)
                        )
                        if not docs:
                            break
                        last_id = docs[-1]["id"]
                        books_index.update_documents(docs)
                        result["books"] += len(docs)
                if need_chapters:
                    last_id = ""
                    while True:
                        docs = await _chapter_docs(
                            select(Chapter.id, Book.kind)
                            .join(Book, Book.id == Chapter.book_id)
                            .where(Chapter.id > last_id)
                            .order_by(Chapter.id)
                            .limit(self.KIND_BACKFILL_BATCH)
                        )
                        if not docs:
                            break
                        last_id = docs[-1]["id"]
                        chapters_index.update_documents(docs)
                        result["chapters"] += len(docs)

        logger.info(
            "Backfilled search kind: {} books, {} chapters",
            result["books"],
            result["chapters"],
        )
        return result

    def delete_book_from_index(self, book_id: str) -> None:
        try:
            self.client.index(self.INDEX_BOOKS).delete_document(book_id)
            logger.debug("Deleted book {} from search index", book_id)
        except Exception:
            pass


search_service = SearchService()
