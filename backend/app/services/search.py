import json
import re
import time

import meilisearch
from loguru import logger

from app.core.config import settings
from app.services.book_kind import KIND_NOVEL, normalize_kind
from app.services.search_ngrams import (
    GRAM_FIELD_FOR_BOOK,
    GRAM_FIELD_FOR_CHAPTER,
    book_gram_fields,
    chapter_gram_fields,
    gram_field_for,
    gram_filter,
)


from app.services.search_index import SearchIndexMixin


class SearchService(SearchIndexMixin):
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
    # Meilisearch's ``pagination.maxTotalHits`` -- the highest rank its ranking
    # rules will ever serve (``_ensure_index`` applies this to both indexes).
    # It is also, therefore, the hard ceiling on how many hits *any* query can
    # see: the engine refuses to return documents past this rank no matter what
    # ``limit`` says, so the old 10 000 meant a query matching 12 466 books
    # reported 10 000 and could not be paged past it.  The live corpus is
    # ~33.5k books / ~117k chapters, so the ceiling is set above both; the gram
    # filter (``search_ngrams``) does the narrowing that the truncated candidate
    # windows used to do, and it can address every match the engine holds.
    # Overridable through ``MEILI_MAX_TOTAL_HITS`` for a library that outgrows it.
    MAX_TOTAL_HITS = 200_000
    # 正文 searches score the engine candidate window in chunks before reporting
    # their total.  This deliberately does **not** follow ``MAX_TOTAL_HITS``: a
    # 正文 scan retrieves the stored body of every candidate, and 10 000 chapters
    # already measured 118 MB / 105 s on the live index, so 117 000 would be
    # ~1.4 GB per search.  The body is the one thing a wider window cannot buy.
    CONTENT_CANDIDATE_LIMIT = 10_000
    CONTENT_SEARCH_LIMIT = CONTENT_CANDIDATE_LIMIT
    CONTENT_RECALL_RETRY_THRESHOLD = 40
    # Per-request candidate count while walking the full body-search window.
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
    # How long a scored single-condition result stays usable for paging.  Books
    # and chapters keep flowing in while a user pages, so this is deliberately
    # short; the cost of a miss is one scan.
    PAGE_CACHE_TTL_SECONDS = 120
    # One entry is at most 10 000 ``(score, id, snippet)`` pairs. Content rows
    # retain only a bounded excerpt, not the chapter bodies, after scoring.
    PAGE_CACHE_MAX_ENTRIES = 16
    CONDITION_CACHE_MAX_ENTRIES = 8
    # ...and an entry is no longer bounded by 10 000 either, now that a
    # gram-narrowed scan can walk a whole index.  Sixteen entries of 200 000 rows
    # would be hundreds of MB in a backend container limited to 1 GB, so both
    # caches are additionally bounded by the number of ranked rows they hold and
    # evict the least recently stored entry until both budgets fit.  Section 28
    # of codex-handoff.md sizes one 33.6k-book ranking at ~5 MB.
    PAGE_CACHE_MAX_ROWS = 250_000
    CONDITION_CACHE_MAX_ROWS = 120_000
    # Multi-condition (AND/OR) searches used to score every condition on its own
    # candidate window and intersect the ids in Python.  For two conditions on
    # the same attribute that is arithmetically hopeless: 正文 「铃」 has 9 619
    # matching chapters and 正文 「仙」 has 10 000+, but their *top* 300 candidates
    # barely overlap, so `正文 铃 AND 正文 仙` returned 0 while the real
    # intersection held 1 299 chapters.  Same-attribute AND now asks the engine
    # for the conjunction (`matchingStrategy: "all"`) instead, which finds real
    # intersections at any depth.  The window below is how many of those ranked
    # hits are scored and cached for paging; the engine narrows candidates before
    # returning ids, and chapter bodies are fetched only for the current page.
    # Like ``CONTENT_SEARCH_LIMIT`` this deliberately does not follow
    # ``MAX_TOTAL_HITS``: the ranked list is held in memory per page-cache entry,
    # and a window as wide as the whole engine ceiling would put hundreds of MB
    # into a backend container limited to 1 GB.
    CONJUNCTION_CANDIDATE_LIMIT = 10_000
    # How many candidates one gram-narrowed condition may walk, per index.  A
    # gram filter makes a chunk selective, but it does not make the *engine*
    # fast: on the production box the chapters index holds 732 462 documents
    # inside a 94 GB store served from 12 GB of RAM, where a zero-query 10 000-row
    # window measured 514-631 s (codex-handoff.md section 51) and a 1 000-row
    # window of an ordinary query measured 8.6 s cold / 0.07 s warm (measured
    # 2026-10-15).  So the two indexes get different budgets -- the books index is
    # 46k small documents and answers a 10 000-row id-only window in 0.5 s hot,
    # while a chapters walk is bounded at ten times what it used to be allowed
    # rather than at the whole library.  Raise the chapters budget only after
    # measuring a real narrowed walk there.
    SCAN_BUDGET_BOOKS = MAX_TOTAL_HITS
    SCAN_BUDGET_CHAPTERS = 10_000
    # Per-request candidate count while walking a *gram-narrowed* metadata scan.
    # Deliberately far larger than ``_SCAN_CHUNK``: Meilisearch re-ranks the whole
    # candidate set for every request (there is no cursor), so a 1 000-row walk
    # over 45 000 books costs 45 rankings of 45 000 documents instead of five.
    # Metadata rows carry no text, and 10 000 rows is the payload the
    # pre-narrowing code already fetched in a single request (section 28 sizes it
    # at ~10 MB), so this is a proven size rather than a new gamble.
    METADATA_SCAN_CHUNK = 10_000
    # Documents per ``update_documents`` call while backfilling the gram fields.
    # The index holds ~732k chapters and Meilisearch processes writes one task at
    # a time behind whatever the crawler is already queueing (section 51 measured
    # 8.2 tasks/min), so a 1 000-document batch turns the backfill into ~740
    # queued tasks and 10 000 turns it into ~74.  It is a merge, so each request
    # carries only the ids and the grams.
    GRAM_BACKFILL_BATCH = 10_000
    # How long "this index is fully backfilled" is trusted before being
    # re-verified.  See ``gram_index_ready``: the check exists so that a writer
    # left on older code cannot silently hide its documents from search.
    GRAM_READY_TTL_SECONDS = 600
    # Postgres advisory-lock key that serialises the gram backfill across the
    # backend's two uvicorn workers (each fires the startup backfill; a duplicate
    # would re-tokenise the whole index twice).
    GRAM_BACKFILL_ADVISORY_LOCK_KEY = 834_265_817
    # Complete small exact result sets are verified before paging; larger sets
    # are checked page by page because verification retrieves stored text.
    CONJUNCTION_VERIFY_MAX_HITS = 200
    # Words returned around the match when a chapter body is used as a snippet.
    SNIPPET_CROP_WORDS = 160
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
    SNIPPET_TAIL_CHARS = 320
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
        # ``owner_id`` / ``is_public`` let the engine apply per-user visibility
        # *before* paginating, so a hidden book no longer shrinks somebody
        # else's page (the route keeps its DB-level check as a safety net for
        # documents indexed before these fields existed).
        "owner_id",
        "is_public",
    ]

    def __init__(self):
        self.client = meilisearch.Client(settings.MEILI_HOST, settings.MEILI_KEY)
        self._chapter_buffer: list[dict] = []
        self._ensured: set[str] = set()
        # Indexes whose gram fields are known to be present on **every** document,
        # so a gram-filtered metadata search cannot hide a not-yet-backfilled
        # book.  ``{index: monotonic deadline}``; populated by
        # ``sync_book_grams`` / ``rebuild_index`` and re-verified by
        # ``gram_index_ready`` once the deadline passes.
        self._gram_ready: dict[str, float] = {}
        self._page_cache: dict[str, tuple[float, list[tuple[int, str, str]]]] = {}
        self._condition_cache: dict[
            str, tuple[float, dict[str, tuple[int, dict]]]
        ] = {}
        # The engine's own hit count for a conjunction query.  It has to be kept
        # with the cached ranking, otherwise page 1 would report the engine's
        # count and page 2 the window's -- the result list would "change" as the
        # user pages, which is exactly the 0-result complaint this replaces.
        self._conjunction_totals: dict[str, tuple[float, int | None]] = {}

    @staticmethod
    def owner_visibility_clause(viewer_id: str | None, is_admin: bool) -> str | None:
        """Meilisearch filter restricting results to what ``viewer`` may see.

        Mirrors ``_is_visible_book`` in the search route: global books
        (``owner_id`` null) are everyone's, private books are the owner's, and
        ``is_public`` books are shared.  ``NOT EXISTS`` covers documents
        indexed before this field existed; anything it wrongly lets through is
        still caught by the route's DB-level safety net until the index is
        rebuilt.
        """
        if is_admin:
            return None
        safe = (viewer_id or "").replace("\\", "\\\\").replace('"', '\\"')
        return (
            f'(owner_id IS NULL OR owner_id NOT EXISTS OR owner_id = "{safe}"'
            " OR is_public = true)"
        )

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
        owner_clause: str | None = None,
    ) -> str | None:
        parts = []
        r18_filter = self._visibility_filter(allow_r18, allow_all_ages)
        if r18_filter:
            parts.append(r18_filter)
        if owner_clause:
            parts.append(owner_clause)
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
        owner_clause: str | None = None,
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
        filters = self._combined_filter(
            allow_r18, allow_all_ages, tag, source_id, kind, owner_clause,
        )
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
        owner_clause: str | None = None,
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
        filters = self._combined_filter(
            allow_r18, allow_all_ages, tag, source_id, kind, owner_clause,
        )
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
        offset: int = 0,
        matching_strategy: str | None = None,
    ) -> dict:
        options = {
            "limit": int(
                limit
                or (self.CONTENT_CANDIDATE_LIMIT if attr == "content"
                    else self.CANDIDATE_LIMIT)
            ),
            "offset": offset,
            "attributesToSearchOn": [attr],
            # Cap the payload: without this a chapter search returned every
            # candidate's full 100 KB body (313 MB for one page click).
            "attributesToRetrieve": self._retrieve_attrs(index_name, attr),
            "showRankingScore": True,
        }
        if matching_strategy:
            options["matchingStrategy"] = matching_strategy
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
        match. The chapters index is two orders of magnitude slower at that
        width (47-250 s measured on the live 115k-chapter index), so chapter
        metadata conditions keep the small window. Content uses the full engine
        candidate window; body queries are fetched in chunks and retained as
        bounded snippets while their scores are combined.
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

    # ---- gram narrowing: the engine does the substring match ----

    @staticmethod
    def _combine_filters(*parts: str | None) -> str | None:
        """AND several optional filters, leaving a lone part byte-identical.

        Existing call sites pass a single expression and their tests pin the
        exact string; wrapping that in parentheses would change what the engine
        receives for no benefit.
        """
        kept = [part for part in parts if part]
        if not kept:
            return None
        if len(kept) == 1:
            return kept[0]
        return " AND ".join(f"({part})" for part in kept)

    def gram_narrowing(
        self,
        index_name: str,
        attr: str,
        value: str,
        mode: str,
    ) -> str | None:
        """The filter that reduces a metadata scan to its real candidates.

        ``None`` whenever the narrowing is not available, and every caller keeps
        its windowed scan in that case:

        * the attribute is not grammed (``description``, ``content``, arrays);
        * the mode is neither ``exact`` nor ``fuzzy``;
        * the query is empty;
        * the index is not backfilled yet, or its state could not be probed.

        Both modes are narrowable, and both are worth it: the library search box
        sends ``fuzzy`` for title/author, so narrowing only ``exact`` would leave
        the most-used search on its truncated window.
        """
        if mode not in ("exact", "fuzzy"):
            return None
        field = gram_field_for(attr)
        if field is None or field not in self.gram_fields(index_name):
            return None
        if not self.gram_index_ready(index_name):
            return None
        return gram_filter(field, value, mode)

    def gram_narrowed_filters(
        self,
        index_name: str,
        attr: str,
        value: str,
        mode: str,
        filters: str | None,
    ) -> str | None:
        """``filters`` AND-ed with the gram narrowing, or ``None`` if unavailable.

        A non-``None`` answer means the engine has already discarded every
        document that cannot match, so the caller may drop its candidate window
        and walk the result until a chunk comes back short.
        """
        narrowing = self.gram_narrowing(index_name, attr, value, mode)
        if not narrowing:
            return None
        return self._combine_filters(filters, narrowing)

    def _collect_condition(
        self,
        index_name: str,
        attr: str,
        value: str,
        mode: str,
        filters: str | None,
        limit: int | None = None,
    ) -> dict[str, tuple[int, dict]]:
        window = (
            limit if limit is not None
            else self._candidate_window(index_name, attr)
        )
        # A fuzzy 正文 condition inside a multi-condition search is answered
        # natively over the whole index (the engine's "all" gate *is* the fuzzy
        # test), so it is no longer cut at the 10 000-candidate window.  This is
        # the OR / 正文+标签 / 正文+简介 residue the native AND path above does not
        # already cover.
        if (
            limit is None
            and index_name == self.INDEX_CHAPTERS
            and attr == "content"
            and mode == "fuzzy"
        ):
            native = self._collect_fuzzy_content_native(value, filters)
            if native is not None:
                return native
        # A gram-narrowed condition behaves like a 正文 scan: the engine has
        # already discarded the non-matches, so the window only bounds the walk
        # and the chunks stay small.  This is what lifts the 1 000-candidate
        # window that hid 99% of the chapter index from a multi-condition search
        # -- ``book_title``/``book_author`` are grammed, so the walk now reaches
        # far more of what the query really matches.
        narrowed = (
            None if limit is not None
            else self.gram_narrowed_filters(index_name, attr, value, mode, filters)
        )
        if narrowed:
            window = self.scan_budget(index_name)
            filters = narrowed
        cache_key = "|".join((
            index_name, attr, value, mode, filters or "", str(window),
        ))
        now = time.monotonic()
        cached = self._condition_cache.get(cache_key)
        if cached is not None:
            expires_at, candidates = cached
            if expires_at >= now:
                return candidates
            self._condition_cache.pop(cache_key, None)
        step = (
            min(self._SCAN_CHUNK, window)
            if attr == "content"
            else (min(self.METADATA_SCAN_CHUNK, window) if narrowed else window)
        )

        def collect(matching_strategy: str | None) -> dict[str, tuple[int, dict]]:
            candidates: dict[str, tuple[int, dict]] = {}
            fetched = 0
            while fetched < window:
                chunk = min(step, window - fetched)
                result = self._search_field(
                    index_name,
                    attr,
                    value,
                    filters,
                    limit=chunk,
                    offset=fetched,
                    matching_strategy=matching_strategy,
                )
                page_hits = result.get("hits", []) or []
                for hit in page_hits:
                    text = hit.get(attr) or ""
                    score = self._condition_score(
                        value,
                        text,
                        mode,
                        hit.get("_rankingScore", 0),
                    )
                    if score <= 0:
                        continue
                    doc_id = str(hit.get("id"))
                    retained_hit = hit
                    if attr == "content":
                        retained_hit = dict(hit)
                        retained_hit[attr] = self._snippet(text, [value])
                    previous = candidates.get(doc_id)
                    if previous is None or score > previous[0]:
                        candidates[doc_id] = (score, retained_hit)
                fetched += len(page_hits)
                if len(page_hits) < chunk:
                    break
            return candidates

        if index_name != self.INDEX_CHAPTERS or attr != "content" or len(value) <= 1:
            candidates = collect(None)
        else:
            try:
                candidates = collect("all")
            except meilisearch.errors.MeilisearchApiError:
                candidates = collect(None)
            else:
                if not candidates or len(candidates) < min(
                    self.CONTENT_RECALL_RETRY_THRESHOLD, window,
                ):
                    for doc_id, candidate in collect(None).items():
                        previous = candidates.get(doc_id)
                        if previous is None or candidate[0] > previous[0]:
                            candidates[doc_id] = candidate

        now = time.monotonic()
        for stale_key in [
            key
            for key, (expires_at, _) in self._condition_cache.items()
            if expires_at < now
        ]:
            self._condition_cache.pop(stale_key, None)
        self._condition_cache[cache_key] = (
            now + self.PAGE_CACHE_TTL_SECONDS, candidates,
        )
        self._evict_to_budget(
            self._condition_cache,
            self.CONDITION_CACHE_MAX_ENTRIES,
            self.CONDITION_CACHE_MAX_ROWS,
        )
        return candidates

    def _collect_fuzzy_content_native(
        self,
        value: str,
        filters: str | None,
    ) -> dict[str, tuple[int, dict]] | None:
        """Fuzzy 正文 candidates for a multi-condition search, whole index.

        Returns ``{doc_id: (score, hit)}`` with light chapter fields only -- the
        engine's "all" gate *is* the fuzzy test, so no body is fetched and no
        Python re-score is needed.  The native ranking is folded into the score
        so the surrounding intersection still ranks by relevance.  ``None`` means
        the engine could not answer it and the caller keeps its scanned path.
        """
        window = self.max_total_hits()
        candidates: dict[str, tuple[int, dict]] = {}
        fetched = 0
        step = self.METADATA_SCAN_CHUNK
        while fetched < window:
            chunk = min(step, window - fetched)
            options = {
                "limit": chunk,
                "offset": fetched,
                "attributesToSearchOn": ["content"],
                "attributesToRetrieve": list(self.CHAPTER_RETRIEVE_ATTRS),
                "showRankingScore": True,
            }
            if filters:
                options["filter"] = filters
            if len(value) > 1:
                options["matchingStrategy"] = "all"
            try:
                result = self.client.index(self.INDEX_CHAPTERS).search(
                    value, options,
                )
            except meilisearch.errors.MeilisearchApiError as exc:
                logger.warning("Native 正文 collect fell back: {}", exc)
                return None
            page_hits = result.get("hits", []) or []
            for hit in page_hits:
                ranking = float(hit.get("_rankingScore") or 0.0)
                score = 10_000 + int(max(0.0, min(ranking, 1.0)) * 1000)
                doc_id = str(hit.get("id"))
                if doc_id not in candidates:
                    candidates[doc_id] = (score, hit)
            fetched += len(page_hits)
            if len(page_hits) < chunk:
                break
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
        """How far a single-condition 正文 scan walks, in candidates.

        Only ``exact`` 正文 reaches this path now (fuzzy 正文 is served natively),
        and an exact match is a *contiguous* substring -- a strict subset of the
        engine's "all characters present" set.  Walking up to the engine ceiling
        removes the 10 000-candidate cut that hid most exact matches, while the
        chunked scan keeps memory bounded.  The offset is intentionally
        irrelevant: every page reuses one cached ranking.
        """
        return self.max_total_hits()

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
        retrieved. Body candidates are read in bounded chunks, scored as they
        arrive, and reduced to a ranked ``(score, id, snippet)`` list for the
        page cache; this avoids retaining full chapter texts after each chunk.

        A window wider than ``chunk_size`` is walked in chunks, so a complete
        正文 candidate set never has to be returned in one large response. A
        short chunk means the engine has nothing left, so the remaining window
        is skipped.

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
        step = self._SCAN_CHUNK if chunk_size is None else max(1, int(chunk_size))
        index = self.client.index(index_name)

        def collect_scored_candidates(
            matching_strategy: str | None,
            scored_by_id: dict[str, tuple[int, str, str]] | None = None,
        ) -> dict[str, tuple[int, str, str]]:
            if scored_by_id is None:
                scored_by_id = {}
            fetched = 0
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
                if matching_strategy:
                    options["matchingStrategy"] = matching_strategy
                page = index.search(value, options)
                page_hits = page.get("hits", []) or []
                for hit in page_hits:
                    text = hit.get(attr) or ""
                    score = self._condition_score(
                        value,
                        text,
                        mode,
                        hit.get("_rankingScore", 0),
                    )
                    if score <= 0:
                        continue
                    doc_id = str(hit.get("id"))
                    candidate = (
                        score,
                        doc_id,
                        self._snippet(text, [value]) if attr == "content" else "",
                    )
                    previous = scored_by_id.get(doc_id)
                    if previous is None or candidate[0] > previous[0]:
                        scored_by_id[doc_id] = candidate
                fetched += len(page_hits)
                if len(page_hits) < chunk:
                    break

            return scored_by_id

        def rank_candidates(
            scored_by_id: dict[str, tuple[int, str, str]],
        ) -> list[tuple[int, str, str]]:
            return sorted(
                scored_by_id.values(),
                key=lambda row: (-row[0], row[1]),
            )

        if index_name != self.INDEX_CHAPTERS or attr != "content" or len(value) <= 1:
            return rank_candidates(collect_scored_candidates(None))

        try:
            scored_by_id = collect_scored_candidates("all")
        except meilisearch.errors.MeilisearchApiError:
            return rank_candidates(collect_scored_candidates(None))

        scored = rank_candidates(scored_by_id)
        if scored and len(scored) >= min(self.CONTENT_RECALL_RETRY_THRESHOLD, window):
            return scored
        collect_scored_candidates(None, scored_by_id)
        return rank_candidates(scored_by_id)

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

    def _native_content_search(
        self,
        query: str,
        filters: str | None,
        offset: int,
        limit: int,
        matched_fields: list[str] | None = None,
    ) -> dict | None:
        """Serve a fuzzy 正文 search natively across the whole chapter index.

        Meilisearch's ``matchingStrategy: "all"`` is exactly the fuzzy gate --
        every character of the query must appear -- and its proximity ranking
        puts chapters that hold the query as one piece first, so the engine
        answers the complete candidate set without transferring a single body.
        Only the requested page is hydrated (bodies cropped around the match).
        ``query`` may be a single value or the space-joined terms of a same-field
        AND; ``matched_fields`` names the conditions for the served hit.

        Returns ``None`` when the engine cannot answer it (settings still
        applying, attribute not searchable yet), so the caller keeps the scanned
        fallback instead of failing the whole search.
        """
        index = self.client.index(self.INDEX_CHAPTERS)
        options = {
            "limit": limit,
            "offset": offset,
            "attributesToSearchOn": ["content"],
            # Display fields only -- fetching ``content`` for every hit is what
            # made a 正文 search move hundreds of MB and take tens of seconds.
            "attributesToRetrieve": list(self.CHAPTER_RETRIEVE_ATTRS),
            "showRankingScore": True,
        }
        if filters:
            options["filter"] = filters
        if len(query.strip()) > 1:
            options["matchingStrategy"] = "all"
        try:
            result = index.search(query, options)
        except meilisearch.errors.MeilisearchApiError as exc:
            logger.warning("Native 正文 search fell back: {}", exc)
            return None

        hits = result.get("hits", []) or []
        ids = [str(hit.get("id")) for hit in hits if hit.get("id") is not None]
        hydrated = (
            self._hydrate_around(self.INDEX_CHAPTERS, ids, query, "content")
            if ids else {}
        )
        serialized: list[dict] = []
        for hit in hits:
            doc = dict(hydrated.get(str(hit.get("id"))) or hit)
            # ``_hydrate_around`` does not request the ranking score, so carry it
            # over from the native hit (which did).
            if doc.get("_rankingScore") is None:
                doc["_rankingScore"] = hit.get("_rankingScore")
            payload = self._serialize_engine_hit(doc, "content", "chapters")
            if matched_fields:
                payload["matched_fields"] = matched_fields
            serialized.append(payload)
        total = result.get("estimatedTotalHits")
        return {
            "hits": serialized,
            "total": int(total) if isinstance(total, int) else len(hits),
            "offset": offset,
            "limit": limit,
        }

    def _native_content_and_metadata_search(
        self,
        active: list[dict],
        filters: str | None,
        offset: int,
        limit: int,
        scope: str,
    ) -> dict | None:
        """One fuzzy 正文 condition AND-ed with filterable metadata.

        ``正文 A AND 作者 B`` is one native "all" query (A) restricted by the
        author's gram filter, and ``正文 A AND 标签 X`` by a ``tags = X`` filter,
        so the engine answers the full intersection without the 10 000-candidate
        window and without transferring bodies.  Returns ``None`` when the shape
        does not qualify -- not exactly one fuzzy content condition, or a partner
        whose field is neither grammed nor filterable (description, category) --
        and the caller keeps the bounded windowed path.
        """
        index_name = self._scope_index(scope)
        content_index: int | None = None
        for i, cond in enumerate(active):
            if cond["field"] != "content":
                continue
            if content_index is not None:
                return None
            if (cond.get("mode") or "exact") != "fuzzy":
                return None
            content_index = i
        if content_index is None:
            return None

        extra: list[str] = []
        for i, cond in enumerate(active):
            if i == content_index:
                continue
            attr = self._field_attr(index_name, cond["field"])
            if not attr:
                return None
            mode = cond.get("mode") or "exact"
            narrowing = self.gram_narrowing(index_name, attr, cond["value"], mode)
            if narrowing:
                extra.append(narrowing)
                continue
            # ``tags`` is not grammed but is filterable as exact membership, so
            # ``正文 A AND 标签 X`` still collapses into one native query.
            if attr == "tags" and mode == "exact":
                safe = cond["value"].replace("\\", "\\\\").replace('"', '\\"')
                extra.append(f'tags = "{safe}"')
                continue
            return None
        combined = self._combine_filters(filters, *extra)
        return self._native_content_search(
            active[content_index]["value"],
            combined,
            offset,
            limit,
            matched_fields=[cond["field"] for cond in active],
        )

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

        # A fuzzy 正文 condition is answered natively by ``matchingStrategy:
        # "all"`` over the whole chapter index -- no 10 000-candidate window, no
        # body transfer.  ``exact`` 正文 still needs the Python scan below, because
        # a bare contiguous substring is the one thing the engine cannot express.
        if attr == "content" and (cond.get("mode") or "exact") == "fuzzy":
            native = self._native_content_search(
                cond["value"], filters, offset, limit,
            )
            if native is not None:
                return native

        window = (
            self._content_window_for(offset) if attr == "content"
            else self.METADATA_CANDIDATE_LIMIT
        )
        # Metadata scans carry no text, so one wide request per page is fine (the
        # 10 000 window is ~10 MB and has always been fetched like that). A 正文
        # scan reads all available candidates in 1 000-row chunks; broad queries
        # cost more, but pagination receives the full verified total rather than
        # stopping at the first 300 candidates.
        chunk_size = self._SCAN_CHUNK if attr == "content" else window
        # A gram-narrowed metadata scan has no ranking window to respect -- the
        # engine already dropped every document that cannot hold the query -- so
        # it walks up to this index's scan budget.  It walks in large steps, not
        # in ``_SCAN_CHUNK`` rows: every request re-ranks the whole candidate set,
        # so many small steps would multiply the engine's work by the number of
        # steps.  The body is the only reason to keep steps small, and a body is
        # never grammed.
        narrowed = self.gram_narrowed_filters(
            index_name, attr, cond["value"], cond.get("mode") or "exact", filters,
        )
        if narrowed:
            window = self.scan_budget(index_name)
            chunk_size = min(self.METADATA_SCAN_CHUNK, window)
            filters = narrowed
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
        self._evict_to_budget(
            self._page_cache,
            self.PAGE_CACHE_MAX_ENTRIES,
            self.PAGE_CACHE_MAX_ROWS,
        )

    @staticmethod
    def _cached_rows(cache: dict) -> int:
        """Total rows held by a ``{key: (expires_at, payload)}`` cache."""
        return sum(len(payload) for _expires_at, payload in cache.values())

    @classmethod
    def _evict_to_budget(cls, cache: dict, max_entries: int, max_rows: int) -> None:
        """Drop the least recently stored entries until both budgets fit.

        Entry count alone stopped being a memory bound once a single cached
        ranking could cover a whole index: what matters is how many scored rows
        are resident.  The entry that was just stored is never the one evicted,
        so a result larger than the whole budget is still served from cache.
        """
        while len(cache) > 1 and (
            len(cache) > max_entries or cls._cached_rows(cache) > max_rows
        ):
            oldest = min(cache.items(), key=lambda item: item[1][0])[0]
            cache.pop(oldest, None)

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

        Exact multi-character values are quoted to improve engine ranking.
        CJK phrase queries can still return scattered character matches, so
        exact-mode hits must be checked against their stored text. Fuzzy values
        stay unquoted: fuzzy only requires every character to appear, which is
        what the bare character tokens already express.
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
    ) -> dict[str, str | None]:
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
        values: dict[str, str | None] = {}
        for hit in result.get("hits", []) or []:
            text = hit.get(attr)
            # No stored value means the field is not retrievable; report it as
            # "unknown" so the caller falls back to the engine's own match.
            values[str(hit.get("id"))] = None if text is None else str(text)
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
        # A fuzzy 正文 AND is one native "all" query (every character of every
        # condition must appear), so it inherits the single-condition fast path:
        # whole index, no body transfer, no 10 000-candidate window.
        if attr == "content" and all(
            (cond.get("mode") or "exact") == "fuzzy" for cond in active
        ):
            native = self._native_content_search(
                self._conjunction_query(active),
                filters,
                offset,
                limit,
                matched_fields=[cond["field"] for cond in active],
            )
            if native is not None:
                return native
        cache_key = "|".join((
            "conj", index_name, attr, self._conjunction_query(active),
            filters or "",
            ",".join(cond.get("mode") or "exact" for cond in active),
            str(self.CONJUNCTION_CANDIDATE_LIMIT),
        ))
        ranked = self._page_cache_get(cache_key)
        verified_text: dict[str, str | None] = {}
        verification_attempted = False
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
            if (
                ranked
                and engine_total is not None
                and engine_total <= len(ranked)
                and len(ranked) <= self.CONJUNCTION_VERIFY_MAX_HITS
                and all((cond.get("mode") or "exact") == "exact" for cond in active)
            ):
                candidate_ids = [row[1] for row in ranked]
                candidate_text = self._conjunction_values(
                    index_name, candidate_ids, attr,
                )
                verification_attempted = True
                verified_text = candidate_text
                if (
                    len(candidate_text) == len(ranked)
                    and all(text is not None for text in candidate_text.values())
                ):
                    verified_ranked = []
                    for _rank_score, doc_id, snippet in ranked:
                        text = candidate_text[doc_id] or ""
                        match_score = sum(
                            1
                            for cond in active
                            if self._condition_score(
                                cond["value"],
                                text,
                                cond.get("mode") or "exact",
                            ) > 0
                        )
                        if match_score == len(active):
                            verified_ranked.append((match_score, doc_id, snippet))
                    ranked = verified_ranked
                    engine_total = len(ranked)
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
        stored = verified_text
        if (
            not verification_attempted
            and page_ids
            and len(page_ids) <= self.CONJUNCTION_VERIFY_MAX_HITS
        ):
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
        # the window cannot serve is worse than a slightly smaller total.  The
        # cap is the *window* (``CONJUNCTION_CANDIDATE_LIMIT``), not the engine's
        # ``maxTotalHits``: the engine ceiling was raised to cover the whole
        # library, and using it here would promise pages that this ranking,
        # deliberately kept small to bound memory, cannot produce.
        total = min(
            engine_total if engine_total is not None else len(ranked),
            max(len(ranked), self.CONJUNCTION_CANDIDATE_LIMIT),
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
        owner_clause: str | None = None,
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
                    allow_r18, allow_all_ages, tag, source_id, kind, owner_clause,
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
            allow_r18, allow_all_ages, tag, source_id, kind, owner_clause,
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

        # One fuzzy 正文 condition AND-ed with gram-narrowable metadata
        # (author/title/chapter title) is one native "all" query plus filters,
        # so it too reaches the whole index instead of the 10 000-candidate
        # window the detached per-condition maps below are bound to.
        if match == "and" and effective_scope == "chapters":
            native_multi = self._native_content_and_metadata_search(
                active, filters, offset, limit, effective_scope,
            )
            if native_multi is not None:
                return native_multi

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

search_service = SearchService()
