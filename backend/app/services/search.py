import re

import meilisearch
from loguru import logger

from app.core.config import settings


class SearchService:
    INDEX_BOOKS = "books"
    INDEX_CHAPTERS = "chapters"
    CHAPTER_BUFFER_SIZE = 100
    CANDIDATE_LIMIT = 5000
    CONTENT_INDEX_LIMIT = 100_000
    DESCRIPTION_INDEX_LIMIT = 2000

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
    FILTERABLE_ATTRIBUTES = ["source_id", "book_id", "author_id", "is_r18", "tags"]

    def __init__(self):
        self.client = meilisearch.Client(settings.MEILI_HOST, settings.MEILI_KEY)
        self._chapter_buffer: list[dict] = []
        self._ensured: set[str] = set()

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
        self.client.index(self.INDEX_BOOKS).add_documents([book])
        logger.debug("Indexed book {}", book.get("id"))

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

    def _combined_filter(
        self,
        allow_r18: bool,
        allow_all_ages: bool,
        tag: str | None,
        source_id: str | None = None,
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
        return " AND ".join(parts) if parts else None

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
        filters = self._combined_filter(allow_r18, allow_all_ages, tag, source_id)
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
        filters = self._combined_filter(allow_r18, allow_all_ages, tag, source_id)
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
            "limit": self.CANDIDATE_LIMIT,
            "offset": 0,
            "attributesToSearchOn": [attr],
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

    def advanced_search(
        self,
        conditions: list[dict],
        *,
        match: str = "and",
        scope: str = "all",
        tag: str | None = None,
        source_id: str | None = None,
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
                    allow_r18, allow_all_ages, tag, source_id,
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
            allow_r18, allow_all_ages, tag, source_id,
        )
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

    def delete_book_from_index(self, book_id: str) -> None:
        try:
            self.client.index(self.INDEX_BOOKS).delete_document(book_id)
            logger.debug("Deleted book {} from search index", book_id)
        except Exception:
            pass


search_service = SearchService()
