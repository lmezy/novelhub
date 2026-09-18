from unittest.mock import MagicMock, patch

import pytest
import requests
from meilisearch.errors import MeilisearchApiError

from app.services.search import SearchService


def _make_service():
    client = MagicMock()
    with patch("app.services.search.meilisearch.Client", return_value=client):
        service = SearchService()
    return service, client


def test_ensure_index_is_cached():
    service, client = _make_service()

    service._ensure_index("books")
    service._ensure_index("books")

    assert client.get_index.call_count == 1
    assert client.index.call_count == 1


def test_ensure_index_updates_settings():
    service, client = _make_service()
    index = client.index.return_value

    service._ensure_index("books")

    index.update_filterable_attributes.assert_called_once()
    index.update_searchable_attributes.assert_called_once()
    index.update_pagination_settings.assert_called_once_with({"maxTotalHits": 10000})


def test_ensure_indexes_creates_missing_indexes():
    service, client = _make_service()
    response = requests.Response()
    response.status_code = 404
    response.encoding = "utf-8"
    response._content = b'{"message":"Index `books` not found."}'
    client.get_index.side_effect = MeilisearchApiError("missing", response)

    service.ensure_indexes()

    assert client.create_index.call_count == 2


def test_get_index_stats_ensures_indexes_first():
    service, client = _make_service()

    service.get_index_stats()

    assert service._ensured == {"books", "chapters"}


def test_search_books_restricts_attributes():
    service, client = _make_service()
    index = client.index.return_value

    service.search_books("西游记")

    options = index.search.call_args.args[1]
    assert options["attributesToSearchOn"] == [
        "title",
        "author",
        "description",
        "tags",
        "category_names",
    ]


def test_candidate_limit_is_bounded_for_fast_advanced_search():
    """The candidate window decides how long every page click takes.

    At 5000 a single search on the live index took 40-110 s (~66 s cold), and
    the candidate fetch also dragged each chapter's full 100 KB body along
    (313 MB per request).  Keep it small; the retrieval attributes keep the
    payload small too.
    """
    assert SearchService.CANDIDATE_LIMIT == 1000
    assert "content" not in SearchService.CHAPTER_RETRIEVE_ATTRS
    assert "content" not in SearchService.BOOK_RETRIEVE_ATTRS


def test_content_search_uses_a_smaller_candidate_window():
    """``content`` is the one field whose candidates must carry the text.

    1000 chapters is ~96 MB of JSON and took 18-25 s on every request against
    the live index; 300 keeps it under a second.
    """
    assert SearchService.CONTENT_CANDIDATE_LIMIT < SearchService.CANDIDATE_LIMIT
    service, client = _make_service()
    index = client.index.return_value
    index.search.return_value = {"hits": []}

    service._search_field(SearchService.INDEX_CHAPTERS, "content", "白", None)
    assert index.search.call_args.args[1]["limit"] == SearchService.CONTENT_CANDIDATE_LIMIT

    service._search_field(SearchService.INDEX_CHAPTERS, "title", "白", None)
    assert index.search.call_args.args[1]["limit"] == SearchService.CANDIDATE_LIMIT


def test_search_field_does_not_retrieve_unrequested_content():
    service, client = _make_service()
    index = client.index.return_value
    index.search.return_value = {"hits": []}

    service._search_field(SearchService.INDEX_CHAPTERS, "title", "白骨精", None)

    options = index.search.call_args.args[1]
    assert "content" not in options["attributesToRetrieve"]
    assert "book_id" in options["attributesToRetrieve"]


def test_search_field_retrieves_content_when_it_is_the_searched_field():
    service, client = _make_service()
    index = client.index.return_value
    index.search.return_value = {"hits": []}

    service._search_field(SearchService.INDEX_CHAPTERS, "content", "白骨精", None)

    options = index.search.call_args.args[1]
    assert "content" in options["attributesToRetrieve"]


def test_advanced_search_applies_kind_filter():
    service, books_index, _ = _service_with_indexes()
    books_index.search.return_value = {"hits": []}

    service.advanced_search(
        [{"field": "title", "mode": "exact", "value": "西游记"}],
        scope="books",
        kind="comic",
    )

    options = books_index.search.call_args.args[1]
    assert options["filter"] == 'kind = "comic"'


def test_kind_filter_ignores_unknown_values():
    assert SearchService._kind_filter("") is None
    assert SearchService._kind_filter("all") is None
    assert SearchService._kind_filter(None) is None
    assert SearchService._kind_filter("novel") == 'kind = "novel"'


def test_index_book_always_carries_a_kind():
    service, client = _make_service()
    index = client.index.return_value

    service.index_book({"id": "b1", "title": "无名"})

    assert index.add_documents.call_args.args[0][0]["kind"] == "novel"

    service.index_book({"id": "b2", "title": "图集", "kind": "comic"})

    assert index.add_documents.call_args.args[0][0]["kind"] == "comic"


def test_index_missing_kind_probes_with_a_exists_filter():
    service, client = _make_service()
    index = client.index.return_value
    index.search.return_value = {"hits": [{"id": "b1"}]}

    assert service.index_missing_kind(SearchService.INDEX_BOOKS) is True

    options = index.search.call_args.args[1]
    assert options["filter"] == "kind NOT EXISTS"
    assert options["limit"] == 1


@pytest.mark.asyncio
async def test_sync_book_kinds_with_no_changed_books_touches_nothing():
    """A re-classification that changed nothing must not re-index the library."""
    service, client = _make_service()

    result = await service.sync_book_kinds(book_ids=[])

    assert result == {"books": 0, "chapters": 0, "skipped": True}
    assert client.index.return_value.update_documents.call_count == 0
    assert client.index.return_value.search.call_count == 0


def _exact_service(hits, hydrated):
    """Service whose books index answers the scan and the hydration query."""
    service, books_index, _ = _service_with_indexes()

    def fake_search(query, options):
        if options.get("filter"):
            return {"hits": hydrated}
        return {"hits": hits}

    books_index.search.side_effect = fake_search
    return service, books_index


def test_exact_single_condition_scans_once_then_serves_pages_from_cache():
    scan_hits = [
        {"id": "b1", "title": "三打白骨精", "_rankingScore": 0.9},
        {"id": "b2", "title": "三打白骨精 续", "_rankingScore": 0.5},
        {"id": "b3", "title": "白龙精", "_rankingScore": 0.9},
    ]
    service, books_index = _exact_service(
        scan_hits,
        [{"id": "b1", "title": "三打白骨精", "author": "吴承恩", "tags": [], "category_names": []}],
    )
    conds = [{"field": "title", "mode": "exact", "value": "白骨精"}]

    first = service.advanced_search(conds, scope="books", offset=0, limit=1)
    scans_after_first = sum(
        1 for call in books_index.search.call_args_list if not call.args[1].get("filter")
    )
    second = service.advanced_search(conds, scope="books", offset=1, limit=1)
    scans_after_second = sum(
        1 for call in books_index.search.call_args_list if not call.args[1].get("filter")
    )

    # Only the substring matches are kept: 白龙精 is dropped although the engine
    # returned it (Meilisearch only tokenizes; it cannot do a substring test).
    assert first["total"] == 2
    assert second["total"] == 2
    assert scans_after_first == 1
    assert scans_after_second == 1, "page 2 must be served from the cached ranking"
    assert first["hits"][0]["title"] == "三打白骨精"
    assert first["hits"][0]["author"] == "吴承恩"


def test_exact_page_is_hydrated_by_id():
    service, books_index = _exact_service(
        [{"id": "b1", "title": "三打白骨精"}],
        [{"id": "b1", "title": "三打白骨精", "author": "吴承恩", "tags": ["仙侠"], "category_names": []}],
    )

    result = service.advanced_search(
        [{"field": "title", "mode": "exact", "value": "白骨精"}],
        scope="books",
    )

    hydrate_options = books_index.search.call_args.args[1]
    assert hydrate_options["filter"] == 'id IN ["b1"]'
    assert result["hits"][0]["author"] == "吴承恩"


def test_exact_content_search_uses_the_small_window():
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.return_value = {"hits": []}

    service.advanced_search(
        [{"field": "content", "mode": "exact", "value": "笔没墨水"}],
        scope="chapters",
    )

    scan_options = chapters_index.search.call_args.args[1]
    assert scan_options["limit"] == SearchService.CONTENT_CANDIDATE_LIMIT


def test_exact_metadata_search_uses_the_wide_window():
    service, books_index, _ = _service_with_indexes()
    books_index.search.return_value = {"hits": []}

    service.advanced_search(
        [{"field": "title", "mode": "exact", "value": "白骨精"}],
        scope="books",
    )

    scan_options = books_index.search.call_args.args[1]
    assert scan_options["limit"] == SearchService.METADATA_CANDIDATE_LIMIT


def test_page_cache_expires():
    service, _ = _make_service()
    service._page_cache_put("k", [(1, "a")])
    assert service._page_cache_get("k") == [(1, "a")]

    expires_at, ranked = service._page_cache["k"]
    service._page_cache["k"] = (expires_at - service.PAGE_CACHE_TTL_SECONDS * 2, ranked)

    assert service._page_cache_get("k") is None


def test_hydration_failure_keeps_the_search_result():
    """``id`` becomes filterable asynchronously; a racing search must not 500."""
    service, books_index, _ = _service_with_indexes()

    response = requests.Response()
    response.status_code = 400
    response.encoding = "utf-8"
    response._content = b'{"message":"Attribute `id` is not filterable."}'

    def fake_search(query, options):
        if options.get("filter"):
            raise MeilisearchApiError("not filterable", response)
        return {"hits": [{"id": "b1", "title": "三打白骨精"}]}

    books_index.search.side_effect = fake_search

    result = service.advanced_search(
        [{"field": "title", "mode": "exact", "value": "白骨精"}],
        scope="books",
    )

    assert result["total"] == 1
    assert result["hits"][0]["id"] == "b1"


def test_chapter_buffer_flushes_in_batches():
    service, client = _make_service()
    index = client.index.return_value

    for i in range(105):
        service.buffer_chapter({"id": str(i)})

    assert index.add_documents.call_count == 1

    service.flush_chapters()

    assert index.add_documents.call_count == 2


def test_search_books_applies_tag_filter():
    service, client = _make_service()
    index = client.index.return_value

    service.search_books("test", tag="wuxia")

    options = index.search.call_args.args[1]
    assert options["filter"] == 'tags = "wuxia"'


def test_advanced_search_applies_source_filter():
    service, books_index, _ = _service_with_indexes()
    books_index.search.return_value = {"hits": []}

    service.advanced_search(
        [{"field": "title", "mode": "exact", "value": "西游记"}],
        scope="books",
        source_id='source"with-quote',
    )

    options = books_index.search.call_args.args[1]
    assert options["filter"] == 'source_id = "source\\"with-quote"'


def _service_with_indexes():
    client = MagicMock()
    books_index = MagicMock()
    chapters_index = MagicMock()
    client.index.side_effect = lambda name: (
        books_index if name == "books" else chapters_index
    )
    with patch("app.services.search.meilisearch.Client", return_value=client):
        service = SearchService()
    return service, books_index, chapters_index


def test_condition_score_exact_requires_substring():
    assert (
        SearchService._condition_score("白骨精", "第1章 三打白骨精", "exact")
        >= 1_000_000
    )
    assert SearchService._condition_score("白骨精", "白龙精", "exact") == 0


def test_condition_score_fuzzy_ranks_by_matched_chars():
    full = SearchService._condition_score("白骨精", "三打白骨精", "fuzzy")
    two = SearchService._condition_score("白骨精", "白龙精", "fuzzy")
    one = SearchService._condition_score("白骨精", "白毛鼠", "fuzzy")

    assert full > two > one > 0
    assert SearchService._condition_score("白骨精", "毛鼠", "fuzzy") == 0


def test_advanced_search_books_scope_and_requires_all_conditions():
    service, books_index, chapters_index = _service_with_indexes()
    books_index.search.return_value = {
        "hits": [{
            "id": "book-1",
            "title": "西游记",
            "author": "吴承恩",
            "description": "古典名著",
            "tags": [],
            "is_r18": False,
        }]
    }
    chapters_index.search.return_value = {
        "hits": [{
            "id": "chapter-1",
            "book_id": "book-1",
            "title": "三打白骨精",
            "book_title": "西游记",
            "book_author": "吴承恩",
            "book_description": "古典名著",
            "content": "老鸡婆",
            "is_r18": False,
        }]
    }

    result = service.advanced_search(
        [
            {"field": "title", "mode": "exact", "value": "西游记"},
            {"field": "chapter_title", "mode": "exact", "value": "白骨精"},
        ],
        match="and",
        scope="books",
    )

    assert result["total"] == 1
    assert result["hits"][0]["type"] == "book"
    assert result["hits"][0]["title"] == "西游记"
    assert result["hits"][0]["snippet"] == ""
    assert result["hits"][0]["matched_fields"] == ["title", "chapter_title"]
    assert result["hits"][0]["matched_chapter"]["title"] == "三打白骨精"
    assert "老鸡婆" in result["hits"][0]["matched_chapter"]["snippet"]


def test_advanced_search_author_scope_all_returns_books_only():
    service, books_index, chapters_index = _service_with_indexes()
    books_index.search.return_value = {
        "hits": [{
            "id": "book-1",
            "title": "西游记",
            "author": "吴承恩",
            "description": "",
            "tags": [],
            "is_r18": False,
        }]
    }
    chapters_index.search.return_value = {
        "hits": [{
            "id": "chapter-1",
            "book_id": "book-1",
            "title": "第一回",
            "book_title": "西游记",
            "book_author": "吴承恩",
            "book_description": "",
            "content": "",
            "is_r18": False,
        }]
    }

    result = service.advanced_search(
        [{"field": "author", "mode": "exact", "value": "吴承恩"}],
        match="and",
        scope="all",
    )

    assert result["total"] == 1
    assert result["hits"][0]["type"] == "book"
    assert result["hits"][0]["title"] == "西游记"


def test_advanced_search_tags_scope_all_returns_books_only():
    service, books_index, chapters_index = _service_with_indexes()
    books_index.search.return_value = {
        "hits": [{
            "id": "book-1",
            "title": "西游记",
            "author": "吴承恩",
            "description": "",
            "tags": ["仙侠"],
            "is_r18": False,
        }]
    }
    chapters_index.search.return_value = {
        "hits": [{
            "id": "chapter-1",
            "book_id": "book-1",
            "title": "第一回",
            "book_title": "西游记",
            "book_author": "吴承恩",
            "book_description": "",
            "content": "",
            "tags": ["仙侠"],
            "is_r18": False,
        }]
    }

    result = service.advanced_search(
        [{"field": "tags", "mode": "exact", "value": "仙侠"}],
        match="and",
        scope="all",
    )

    assert result["total"] == 1
    assert result["hits"][0]["type"] == "book"
    assert result["hits"][0]["matched_fields"] == ["tags"]
    assert result["hits"][0]["snippet"] == ""


def test_advanced_search_content_scope_all_returns_chapters_only():
    service, books_index, chapters_index = _service_with_indexes()
    books_index.search.return_value = {
        "hits": [{
            "id": "book-1",
            "title": "1983",
            "author": "",
            "description": "笔没墨水",
            "tags": [],
            "is_r18": False,
        }]
    }
    chapters_index.search.return_value = {
        "hits": [{
            "id": "chapter-1",
            "book_id": "book-1",
            "title": "第171章",
            "book_title": "1983",
            "book_author": "",
            "book_description": "",
            "content": "笔没墨水，画像师继续画。",
            "is_r18": False,
        }]
    }

    result = service.advanced_search(
        [{"field": "content", "mode": "exact", "value": "笔"}],
        match="and",
        scope="all",
    )

    assert result["total"] == 1
    assert result["hits"][0]["type"] == "chapter"
    assert result["hits"][0]["book_title"] == "1983"


def test_advanced_search_chapters_fuzzy_is_engine_paged():
    """A single fuzzy condition is answered by Meilisearch itself.

    That is what makes hundreds of pages possible: the engine jumps to the page
    instead of the old "pull the whole candidate window into Python and re-sort
    it on every click" (40-110 s per page on the live index).
    """
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.return_value = {
        "hits": [
            {"id": "c1", "book_id": "b1", "title": "三打白骨精", "book_title": "西游记"},
            {"id": "c2", "book_id": "b2", "title": "白龙精", "book_title": "另一本书"},
        ],
        "totalHits": 2500,
        "totalPages": 63,
    }

    result = service.advanced_search(
        [{"field": "chapter_title", "mode": "fuzzy", "value": "白骨精"}],
        match="or",
        scope="chapters",
        offset=80,
        limit=40,
    )

    options = chapters_index.search.call_args.args[1]
    assert options["page"] == 3
    assert options["hitsPerPage"] == 40
    assert options["attributesToSearchOn"] == ["title"]
    # Names come from the engine, not from a Python-side count of scored hits.
    assert result["total"] == 2500
    assert result["engine_paged"] is True
    assert [hit["title"] for hit in result["hits"]] == ["三打白骨精", "白龙精"]
    assert result["hits"][0]["type"] == "chapter"


def test_engine_paged_deep_page_does_not_scan_candidates():
    service, books_index, _ = _service_with_indexes()
    books_index.search.return_value = {"hits": [], "totalHits": 10000}

    service.advanced_search(
        [{"field": "title", "mode": "fuzzy", "value": "白"}],
        scope="books",
        offset=9960,
        limit=40,
    )

    options = books_index.search.call_args.args[1]
    assert options["page"] == 250
    # One query for the page -- no candidate window, no scoring pass.
    assert "limit" not in options


def test_advanced_search_multi_condition_fuzzy_ranks_in_python():
    """Two conditions still need the candidate window and the shared scorer."""
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.return_value = {
        "hits": [
            {"id": "c-full", "book_id": "b1", "title": "三打白骨精", "book_title": "西游记", "content": ""},
            {"id": "c-two", "book_id": "b2", "title": "白龙精", "book_title": "另一本书", "content": ""},
            {"id": "c-one", "book_id": "b3", "title": "白毛鼠", "book_title": "再一本书", "content": ""},
        ]
    }

    result = service.advanced_search(
        [
            {"field": "chapter_title", "mode": "fuzzy", "value": "白骨精"},
            {"field": "chapter_title", "mode": "fuzzy", "value": "白"},
        ],
        match="or",
        scope="chapters",
    )

    assert result.get("engine_paged") is None
    assert result["total"] == 3
    assert [hit["title"] for hit in result["hits"]] == [
        "三打白骨精",
        "白龙精",
        "白毛鼠",
    ]


def test_advanced_search_or_returns_any_matching_book():
    service, books_index, chapters_index = _service_with_indexes()
    books_index.search.side_effect = [
        {"hits": [{
            "id": "book-1",
            "title": "西游记",
            "author": "吴承恩",
            "description": "",
            "tags": [],
            "is_r18": False,
        }]},
        {"hits": [{
            "id": "book-2",
            "title": "红楼梦",
            "author": "曹雪芹",
            "description": "",
            "tags": [],
            "is_r18": False,
        }]},
    ]
    chapters_index.search.return_value = {"hits": []}

    result = service.advanced_search(
        [
            {"field": "title", "mode": "exact", "value": "西游记"},
            {"field": "author", "mode": "exact", "value": "曹雪芹"},
        ],
        match="or",
        scope="books",
    )

    assert result["total"] == 2
    assert {hit["title"] for hit in result["hits"]} == {"西游记", "红楼梦"}


def test_advanced_search_tag_only_returns_books():
    service, books_index, chapters_index = _service_with_indexes()
    books_index.search.return_value = {
        "hits": [{
            "id": "book-1",
            "title": "西游记",
            "author": "吴承恩",
            "description": "",
            "tags": ["wuxia"],
            "is_r18": False,
        }]
    }

    result = service.advanced_search([], scope="books", tag="wuxia")

    assert result["total"] == 1
    assert result["hits"][0]["type"] == "book"
    assert result["hits"][0]["title"] == "西游记"


def test_advanced_search_tags_condition_matches_book_tags():
    service, books_index, chapters_index = _service_with_indexes()
    books_index.search.return_value = {
        "hits": [{
            "id": "book-1",
            "title": "西游记",
            "author": "吴承恩",
            "description": "",
            "tags": ["仙侠", "古典"],
            "is_r18": False,
        }]
    }

    result = service.advanced_search(
        [{"field": "tags", "mode": "exact", "value": "仙侠"}],
        match="and",
        scope="books",
    )

    assert result["total"] == 1
    assert result["hits"][0]["matched_fields"] == ["tags"]


def test_advanced_search_category_condition_matches_book_categories():
    service, books_index, chapters_index = _service_with_indexes()
    books_index.search.return_value = {
        "hits": [{
            "id": "book-1",
            "title": "仙侠录",
            "author": "",
            "description": "",
            "tags": [],
            "category_names": ["玄幻", "武侠"],
            "is_r18": False,
        }]
    }

    result = service.advanced_search(
        [{"field": "category", "mode": "exact", "value": "玄幻"}],
        match="and",
        scope="books",
    )

    assert result["total"] == 1
    assert result["hits"][0]["category_names"] == ["玄幻", "武侠"]
    assert result["hits"][0]["matched_fields"] == ["category"]
