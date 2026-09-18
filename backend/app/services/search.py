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
    CONTENT_CANDIDATE_LIMIT = 300
    # Single-condition *exact* searches score in Python (see
    # ``_single_condition_search``) because Meilisearch cannot express a Chinese
    # substring match: charabia splits 白骨精 into 白/骨/精, ``matchingStrategy:
    # "last"`` only requires the last fragment (so 穿成白骨肿么破 matches on 破!)
    # and ``"all"``/phrase queries return 0 hits for perfectly good terms such as
    # 剑来.  The scan only retrieves ``id`` + the searched field, which is 0.02-0.7 s
    # for 10 000 documents, and the ordered result is cached for repeat pages.
    METADATA_CANDIDATE_LIMIT = 10_000
    # How long a scored single-condition result stays usable for paging.  Books
    # and chapters keep flowing in while a user pages, so this is deliberately
    # short; the cost of a miss is one scan.
    PAGE_CACHE_TTL_SECONDS = 120
    # One entry is at most ~10 000 ``(score, id)`` pairs (~1.5 MB), so 16 keeps
    # the worst case around 24 MB per process.
    PAGE_CACHE_MAX_ENTRIES = 16
    # Words returned around the match when a chapter body is used as a snippet.
    SNIPPET_CROP_WORDS = 60
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
        self._page_cache: dict[str, tuple[float, list[tuple[int, str]]]] = {}

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
            index.update_pagination_settings({"maxTotalHits": 10000})
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
    def _condition_score(
        value: str,
        text: str,
        mode: str,
        ranking_score: float = 0.0,
    ) -> int:
        """Score one field condition. Exact requires the whole string to appear.

        Fuzzy counts how many distinct characters from the query appear in the
        field, so 白骨精 ranks above 白龙精 (2/3 chars) and 白毛鼠 (1/3 chars).
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
        matched = sum(1 for ch in chars if ch.lower() in haystack)
        if matched == 0:
            return 0
        full_bonus = 1000 if needle in haystack else 0
        return matched * 10_000 + full_bonus + tie

    def _search_field(
        self,
        index_name: str,
        attr: str,
        value: str,
        filters: str | None,
    ) -> dict:
        options = {
            "limit": (
                self.CONTENT_CANDIDATE_LIMIT if attr == "content"
                else self.CANDIDATE_LIMIT
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
    ) -> dict[str, tuple[int, dict]]:
        result = self._search_field(index_name, attr, value, filters)
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

    @staticmethod
    def _snippet(text: str, values: list[str], radius: int = 80) -> str:
        clean = re.sub(r"\s+", " ", text or "").strip()
        if not clean:
            return ""
        lowered = clean.lower()
        for value in values:
            needle = value.strip().lower()
            if not needle:
                continue
            idx = lowered.find(needle)
            if idx >= 0:
                start = max(0, idx - radius)
                end = min(len(clean), idx + len(needle) + radius)
                prefix = "..." if start > 0 else ""
                suffix = "..." if end < len(clean) else ""
                return f"{prefix}{clean[start:end]}{suffix}"
        for value in values:
            for ch in value:
                if not ch.isspace():
                    idx = lowered.find(ch.lower())
                    if idx >= 0:
                        start = max(0, idx - radius)
                        end = min(len(clean), idx + 1 + radius)
                        prefix = "..." if start > 0 else ""
                        suffix = "..." if end < len(clean) else ""
                        return f"{prefix}{clean[start:end]}{suffix}"
        return clean[: radius * 2]

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

    # ---- single-condition search: deep paging without a candidate window ----

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

    def _engine_page(
        self,
        index_name: str,
        value: str,
        attr: str,
        filters: str | None,
        offset: int,
        limit: int,
    ) -> dict:
        """One page straight out of Meilisearch: matching + ranking + paging.

        ``page``/``hitsPerPage`` is Meilisearch's finite-pagination mode and is
        what makes hundreds of pages possible: the response carries the exact
        ``totalHits``/``totalPages`` and the engine can jump to page 250 without
        touching pages 1-249 (measured 0.007 s on the live index, versus 40-110 s
        for the old "pull every candidate into Python" page).
        """
        remainder = offset % limit if limit else 0
        page = offset // limit + 1 if limit else 1
        hits_per_page = limit + remainder
        options = {
            "page": page,
            "hitsPerPage": hits_per_page,
            "attributesToSearchOn": [attr],
            # Display attributes only; ``content`` is cropped below so a
            # chapter page never ships a whole 100 KB body per result.
            "attributesToRetrieve": self._retrieve_attrs(index_name, ""),
            "showRankingScore": True,
        }
        if index_name == self.INDEX_CHAPTERS:
            options["attributesToCrop"] = ["content"]
            options["cropLength"] = self.SNIPPET_CROP_WORDS
        if filters:
            options["filter"] = filters
        result = self.client.index(index_name).search(value, options)
        hits = result.get("hits", []) or []
        if remainder:
            hits = hits[remainder:]
        return {
            "hits": hits,
            "total": int(
                result.get("totalHits")
                or result.get("estimatedTotalHits")
                or len(hits)
            ),
            "offset": offset,
            "limit": limit,
        }

    def _scan_condition(
        self,
        index_name: str,
        attr: str,
        value: str,
        filters: str | None,
        window: int,
    ) -> list[tuple[int, str]]:
        """Rank the engine's candidates with the shared Python scorer.

        Only ``id``, the searched field and the cheapest display fields are
        retrieved, so a 10 000 document window costs 0.02-0.7 s instead of the
        96 MB / 18 s that pulling every chapter body used to cost.  The result is
        a ranked ``(score, id)`` list -- tiny enough to cache, which is what
        makes pages 2..N cheap.
        """
        retrieve = ["id", attr]
        if index_name == self.INDEX_BOOKS:
            retrieve.append("author")
        else:
            retrieve.extend(["book_id", "book_title"])
        options = {
            "limit": window,
            "offset": 0,
            "attributesToSearchOn": [attr],
            "attributesToRetrieve": list(dict.fromkeys(retrieve)),
            "showRankingScore": True,
        }
        if filters:
            options["filter"] = filters
        result = self.client.index(index_name).search(value, options)
        scored: list[tuple[int, str]] = []
        for hit in result.get("hits", []) or []:
            score = self._condition_score(
                value,
                hit.get(attr) or "",
                "exact",
                hit.get("_rankingScore", 0),
            )
            if score > 0:
                scored.append((score, str(hit.get("id"))))
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

    @staticmethod
    def _cropped_content(hit: dict) -> str:
        formatted = hit.get("_formatted") or {}
        return str(formatted.get("content") or "")

    def _serialize_engine_hit(
        self,
        hit: dict,
        field: str,
        scope: str,
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
        content = self._cropped_content(hit)
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

        * ``fuzzy`` -- Meilisearch matches, ranks and pages (its relevance is at
          least as good as the old character-coverage score, and the candidate
          set is the same one the scorer saw).
        * ``exact`` -- Meilisearch can only tokenize, so the substring test stays
          in Python; the engine still drives the scan and the ranked ids are
          cached, which is what makes pages 2..N cheap.
        """
        index_name = self._scope_index(scope)
        attr = self._field_attr(index_name, cond["field"])
        if not attr:
            return None

        if cond["mode"] == "fuzzy":
            page = self._engine_page(
                index_name, cond["value"], attr, filters, offset, limit,
            )
            hits = [
                self._serialize_engine_hit(hit, cond["field"], scope)
                for hit in page["hits"]
            ]
            return {
                "hits": hits,
                "total": page["total"],
                "offset": offset,
                "limit": limit,
                "engine_paged": True,
            }

        window = (
            self.CONTENT_CANDIDATE_LIMIT if attr == "content"
            else self.METADATA_CANDIDATE_LIMIT
        )
        cache_key = "|".join((
            index_name, attr, cond["value"], filters or "", str(window),
        ))
        ranked = self._page_cache_get(cache_key)
        if ranked is None:
            ranked = self._scan_condition(
                index_name, attr, cond["value"], filters, window,
            )
            self._page_cache_put(cache_key, ranked)

        page_rows = ranked[offset : offset + limit]
        docs = self._hydrate(index_name, [row[1] for row in page_rows])
        hits = []
        for _score, doc_id in page_rows:
            hit = docs.get(doc_id)
            if hit is None:
                # Hydration unavailable (settings task still applying): keep the
                # row with the little we know rather than dropping the result.
                hit = {"id": doc_id}
            hits.append(self._serialize_engine_hit(hit, cond["field"], scope))
        return {
            "hits": hits,
            "total": len(ranked),
            "offset": offset,
            "limit": limit,
            "engine_paged": True,
        }

    def _page_cache_get(self, key: str) -> list[tuple[int, str]] | None:
        entry = self._page_cache.get(key)
        if entry is None:
            return None
        expires_at, ranked = entry
        if expires_at < time.monotonic():
            self._page_cache.pop(key, None)
            return None
        return ranked

    def _page_cache_put(self, key: str, ranked: list[tuple[int, str]]) -> None:
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
