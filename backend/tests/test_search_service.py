import time
from unittest.mock import MagicMock, patch

import pytest
import requests
from meilisearch.errors import MeilisearchApiError

from app.core.config import settings
from app.services.search import SearchService
from app.services.search_ngrams import field_grams


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
    index.update_pagination_settings.assert_called_once_with(
        {"maxTotalHits": service.max_total_hits()}
    )


def test_engine_ceiling_covers_the_whole_library_and_is_configurable():
    """The engine refuses to serve documents past ``maxTotalHits``.

    At 10 000 it was both the paging limit and the hard cap on hits a search
    could ever see: a query matching 12 466 books reported 10 000 and could not
    be paged further, no matter what the candidate windows did.  The default has
    to sit above the live corpus (~33.5k books / ~117k chapters), and a
    deployment that outgrows it must be able to raise it without a code change.
    """
    service, _ = _make_service()
    assert SearchService.MAX_TOTAL_HITS >= 200_000
    assert service.max_total_hits() == SearchService.MAX_TOTAL_HITS

    with patch.object(settings, "MEILI_MAX_TOTAL_HITS", 500_000):
        assert service.max_total_hits() == 500_000
    # A nonsense override must not shrink the ceiling below the class default.
    with patch.object(settings, "MEILI_MAX_TOTAL_HITS", 0):
        assert service.max_total_hits() == SearchService.MAX_TOTAL_HITS
    with patch.object(settings, "MEILI_MAX_TOTAL_HITS", "lots"):
        assert service.max_total_hits() == SearchService.MAX_TOTAL_HITS


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


def test_content_search_uses_its_own_candidate_window():
    """The 正文 window is bounded by bandwidth, not by the engine's ceiling.

    It used to be written as ``= MAX_TOTAL_HITS``, which was fine while that
    ceiling was 10 000 and became a 1.4 GB-per-search footgun the moment the
    ceiling was raised to cover the whole library: a 正文 scan retrieves every
    candidate's stored body, so widening it must be a deliberate act rather than
    a side effect of raising the engine's paging limit.
    """
    assert SearchService.CONTENT_CANDIDATE_LIMIT == SearchService.CONTENT_SEARCH_LIMIT
    assert SearchService.CONTENT_CANDIDATE_LIMIT < SearchService.MAX_TOTAL_HITS
    assert SearchService.CONJUNCTION_CANDIDATE_LIMIT < SearchService.MAX_TOTAL_HITS
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


def test_exact_content_search_scans_the_full_engine_window_in_chunks():
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.return_value = {"hits": []}

    service.advanced_search(
        [{"field": "content", "mode": "exact", "value": "笔没墨水"}],
        scope="chapters",
    )

    scan_options = chapters_index.search.call_args.args[1]
    assert service._content_window_for(0) == SearchService.CONTENT_SEARCH_LIMIT
    assert scan_options["limit"] == SearchService._SCAN_CHUNK


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
    service._page_cache_put("k", [(1, "a", "")])
    assert service._page_cache_get("k") == [(1, "a", "")]

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


def test_condition_score_fuzzy_requires_every_character():
    """Fuzzy means "all characters present", not "one of them".

    Meilisearch's CJK analysis is per-character, so 铃铛 came back with 19 hits
    of which exactly one contained 铃铛: the rest had merely 铃 (铃木/铃雨/
    聖誕鈴聲) or merely 铛, which users correctly read as unrelated results.
    """
    assert SearchService._condition_score("铃铛", "【小铃铛】（1-12）", "fuzzy") > 0
    assert SearchService._condition_score("铃铛", "【日娱猎手】铃木爱理", "fuzzy") == 0
    assert SearchService._condition_score("铃铛", "铛的一声", "fuzzy") == 0
    assert SearchService._condition_score("铃铛", "聖誕鈴聲", "fuzzy") == 0
    assert SearchService._condition_score("铃铛", "毛鼠", "fuzzy") == 0


def test_condition_score_fuzzy_prefers_the_whole_string_then_a_long_run():
    contiguous = SearchService._condition_score("白骨精", "第1章 三打白骨精", "fuzzy")
    long_run = SearchService._condition_score("白骨精", "白骨X精", "fuzzy")
    scattered = SearchService._condition_score("白骨精", "白X骨X精", "fuzzy")

    # Same coverage, so the whole-string bonus decides, then the longest
    # contiguous piece (「白骨」 beats 白…骨…精).
    assert contiguous > long_run > scattered > 0
    assert SearchService._longest_run("白骨精", "白骨X精") == 2
    assert SearchService._longest_run("白骨精", "白X骨X精") == 1
    assert SearchService._longest_run("白骨精", "毛鼠") == 0


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


def test_content_search_serves_the_match_and_not_the_head_of_the_chapter():
    """A 正文 hit must show the searched word, not the chapter's opening lines.

    The page is hydrated with an *empty* query so that no hit is lost, and
    Meilisearch only crops around the query terms -- so the excerpt it returns
    for a body search is the head of the chapter.  Measured on the live index,
    3 of 3 hits for 老鸡婆 served a snippet that did not contain 老鸡婆 at all,
    which is the "the result does not show what I searched for" report.
    """
    service, _, chapters_index = _service_with_indexes()
    body = "开" * 800 + "老鸡婆" + "尾" * 400
    head = body[:120]

    def _search(query, options):
        if options.get("filter"):
            # The engine's crop for an empty query: the head of the chapter.
            return {"hits": [{
                "id": "c1", "book_id": "b1", "title": "第1章", "book_title": "某书",
                "_formatted": {"content": head},
            }]}
        return {"hits": [{"id": "c1", "content": body}]}

    chapters_index.search.side_effect = _search

    result = service.advanced_search(
        [{"field": "content", "mode": "exact", "value": "老鸡婆"}],
        match="and",
        scope="all",
    )

    snippet = result["hits"][0]["snippet"]
    assert "老鸡婆" in snippet
    # Keep the hit near the front so list clamping never hides the keyword.
    assert snippet.index("老鸡婆") <= SearchService.SNIPPET_LEAD_CHARS + len("...")
    assert body[:50] not in snippet, "the chapter head is not the hit"
    assert len(snippet) > SearchService.SNIPPET_TAIL_CHARS, "context after the match"


def test_scan_condition_builds_snippets_for_the_body_only():
    """Only a body scan carries an excerpt; metadata rows keep the cache small."""
    service, _, chapters_index = _service_with_indexes()
    body = "甲" * 300 + "铃"
    chapters_index.search.return_value = {
        "hits": [{"id": "c1", "title": "第一章", "content": body}]
    }

    content_rows = service._scan_condition("chapters", "content", "铃", None, 10)
    title_rows = service._scan_condition("chapters", "title", "第一章", None, 10)

    assert [(score > 0, doc_id) for score, doc_id, _ in content_rows] == [(True, "c1")]
    assert content_rows[0][2].startswith("...") and content_rows[0][2].endswith("铃")
    assert title_rows[0][2] == ""


def test_snippet_is_anchored_at_the_match_and_marks_both_cuts():
    body = "甲" * 500 + "老鸡婆" + "乙" * 500

    snippet = SearchService._snippet(body, ["老鸡婆"])

    assert snippet.startswith("...") and snippet.endswith("...")
    assert snippet.count("老鸡婆") == 1
    assert snippet.index("老鸡婆") <= SearchService.SNIPPET_LEAD_CHARS + len("...")
    assert len(snippet) == (
        len("...") * 2
        + SearchService.SNIPPET_LEAD_CHARS
        + len("老鸡婆")
        + SearchService.SNIPPET_TAIL_CHARS
    )


def test_content_window_covers_the_full_content_candidate_limit():
    """All content-search pages use one fully scanned ranking window."""
    service, _ = _make_service()

    assert service._content_window_for(0) == SearchService.CONTENT_SEARCH_LIMIT
    assert service._content_window_for(320) == SearchService.CONTENT_SEARCH_LIMIT
    assert service._content_window_for(5000) == SearchService.CONTENT_SEARCH_LIMIT


def test_content_window_only_applies_to_the_body():
    """Only a 正文 scan is chunked; metadata keeps its single wide request."""
    service, client = _make_service()
    index = client.index.return_value
    index.search.return_value = {"hits": []}

    # A 正文 search scans the complete engine window in bounded chunks.
    service._single_condition_search(
        {"field": "content", "mode": "exact", "value": "铃"},
        scope="chapters", filters=None, offset=1320, limit=40,
    )
    content_call = index.search.call_args_list[0].args[1]

    index.search.reset_mock()
    # The same offset on a metadata field is one request for its 10 000-wide
    # window -- there is no text to carry.
    service._single_condition_search(
        {"field": "title", "mode": "exact", "value": "铃"},
        scope="chapters", filters=None, offset=1320, limit=40,
    )
    title_call = index.search.call_args_list[0].args[1]

    assert content_call["offset"] == 0
    assert content_call["limit"] == SearchService._SCAN_CHUNK
    assert title_call["offset"] == 0
    assert title_call["limit"] == SearchService.METADATA_CANDIDATE_LIMIT


def test_single_condition_content_search_scans_past_the_old_300_limit():
    service, _, chapters_index = _service_with_indexes()
    chapter_count = 1250
    chapter_docs = [
        {
            "id": f"c{i}",
            "book_id": "b1",
            "title": f"第{i}章",
            "book_title": "测试书",
            "content": f"正文命中铃字 {i}",
        }
        for i in range(chapter_count)
    ]

    def engine_page(query, options):
        if options.get("filter"):
            return {"hits": chapter_docs}
        start = options["offset"]
        end = min(start + options["limit"], chapter_count)
        return {"hits": chapter_docs[start:end]}

    chapters_index.search.side_effect = engine_page

    result = service.advanced_search(
        [{"field": "content", "mode": "exact", "value": "铃"}],
        scope="chapters",
        offset=0,
        limit=40,
    )

    assert result["total"] == chapter_count
    assert len(result["hits"]) == 40
    scan_calls = [
        call.args[1]
        for call in chapters_index.search.call_args_list
        if not call.args[1].get("filter")
    ]
    assert [(call["offset"], call["limit"]) for call in scan_calls] == [
        (0, SearchService._SCAN_CHUNK),
        (SearchService._SCAN_CHUNK, SearchService._SCAN_CHUNK),
    ]


def test_multi_condition_content_candidates_scan_in_chunks_and_keep_snippets():
    service, _, chapters_index = _service_with_indexes()
    chapter_count = 1250
    chapter_docs = [
        {
            "id": f"c{i}",
            "book_id": "b1",
            "title": f"第{i}章",
            "book_title": "测试书",
            "content": "甲" * 500 + "铃" + "乙" * 500,
        }
        for i in range(chapter_count)
    ]

    def engine_page(query, options):
        start = options["offset"]
        end = min(start + options["limit"], chapter_count)
        return {"hits": chapter_docs[start:end]}

    chapters_index.search.side_effect = engine_page

    candidates = service._collect_condition(
        "chapters", "content", "铃", "exact", None,
    )

    assert len(candidates) == chapter_count
    snippet = candidates["c0"][1]["content"]
    assert "铃" in snippet
    assert len(snippet) < len(chapter_docs[0]["content"])
    scan_calls = [call.args[1] for call in chapters_index.search.call_args_list]
    assert [(call["offset"], call["limit"]) for call in scan_calls] == [
        (0, SearchService._SCAN_CHUNK),
        (SearchService._SCAN_CHUNK, SearchService._SCAN_CHUNK),
    ]
    cached = service._collect_condition(
        "chapters", "content", "铃", "exact", None,
    )
    assert cached is candidates
    assert len(chapters_index.search.call_args_list) == len(scan_calls)


def test_a_window_wider_than_one_engine_page_is_scanned_in_chunks():
    """A wide body window is walked page by page, not in one huge request.

    Meilisearch will not return a 2 000-document body-carrying page in one
    answer, and a short page means the engine has nothing left to give.
    """
    service, _, chapters_index = _service_with_indexes()

    def engine_page(query, options):
        start = options["offset"]
        limit = options["limit"]
        hits = [
            {"id": f"c{i}", "content": "铃" * 3}
            for i in range(start, min(start + limit, 600))
        ]
        return {"hits": hits}

    chapters_index.search.side_effect = engine_page

    rows = service._scan_condition(
        "chapters", "content", "铃", None, 900, chunk_size=250,
    )

    # 600 candidates exist, so the 900 window stops after the page that ran short
    # (250 + 250 + 100 = 600) instead of asking for the remaining 300.
    offsets = [call.args[1]["offset"] for call in chapters_index.search.call_args_list]
    assert offsets == [0, 250, 500]
    # Every candidate is scored once and the ranked list carries no duplicates.
    ids = [doc_id for _score, doc_id, _snippet in rows]
    assert len(ids) == len(set(ids)) == 600
    assert ids[0] == "c0"  # equal scores tie-break on the id, so order is stable


def test_advanced_search_fuzzy_single_condition_is_scored_not_engine_paged():
    """A single fuzzy condition must be filtered, not handed straight back.

    The engine answers 铃铛 with every title holding 铃 *or* 铛 (per-character CJK
    matching), so its hits -- and its ``totalHits`` -- are not the result set.
    """
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.side_effect = [
        {"hits": [
            {"id": "c1", "book_id": "b1", "title": "小铃铛", "book_title": "西游记"},
            {"id": "c2", "book_id": "b2", "title": "铃木爱理", "book_title": "另一本书"},
            {"id": "c3", "book_id": "b3", "title": "铛的一声", "book_title": "再一本书"},
        ], "estimatedTotalHits": 19},
        {"hits": [
            {"id": "c1", "book_id": "b1", "title": "小铃铛", "book_title": "西游记",
             "content": "…"},
        ]},
    ]

    result = service.advanced_search(
        [{"field": "chapter_title", "mode": "fuzzy", "value": "铃铛"}],
        match="or",
        scope="chapters",
    )

    assert result["total"] == 1
    assert [hit["title"] for hit in result["hits"]] == ["小铃铛"]
    assert result["hits"][0]["type"] == "chapter"


def test_fuzzy_content_single_condition_is_served_natively_full_index():
    """A fuzzy 正文 condition is answered natively, over the whole index.

    ``matchingStrategy: "all"`` is the fuzzy gate, so the engine returns the
    complete candidate set -- no 10 000-candidate window -- and, crucially,
    without transferring chapter bodies: the native query retrieves the display
    fields only, and just the requested page is hydrated (bodies cropped around
    the match).
    """
    service, _, chapters_index = _service_with_indexes()

    def fake_search(query, options):
        if options.get("attributesToCrop"):
            return {"hits": [
                {"id": "c1", "title": "第1章 小铃铛", "book_title": "西游记",
                 "_formatted": {"content": "…村口挂着一只小铃铛…"}},
            ]}
        return {
            "hits": [
                {"id": "c1", "book_id": "b1", "title": "第1章 小铃铛",
                 "book_title": "西游记", "_rankingScore": 0.8},
            ],
            "estimatedTotalHits": 42,
        }

    chapters_index.search.side_effect = fake_search

    result = service.advanced_search(
        [{"field": "content", "mode": "fuzzy", "value": "铃铛"}],
        scope="chapters",
    )

    native_call = chapters_index.search.call_args_list[0]
    options = native_call.args[1]
    assert options["matchingStrategy"] == "all"
    assert options["attributesToSearchOn"] == ["content"]
    assert "content" not in options["attributesToRetrieve"]
    assert result["total"] == 42
    assert result["hits"][0]["type"] == "chapter"
    assert "铃铛" in result["hits"][0]["snippet"]


def test_fuzzy_content_and_is_served_natively_full_index():
    """A same-field fuzzy 正文 AND inherits the native fast path.

    ``铃铛 AND 仙`` is one "all" query over the whole index, so it must not fall
    back to the 10 000-candidate conjunction window, and must not transfer
    chapter bodies.  ``matched_fields`` names every condition.
    """
    service, _, chapters_index = _service_with_indexes()

    def fake_search(query, options):
        if options.get("attributesToCrop"):
            return {"hits": [
                {"id": "c1", "title": "第一章", "book_title": "西游记",
                 "_formatted": {"content": "…铃铛…仙人…"}},
            ]}
        return {
            "hits": [
                {"id": "c1", "book_id": "b1", "title": "第一章",
                 "book_title": "西游记", "_rankingScore": 0.7},
            ],
            "estimatedTotalHits": 9,
        }

    chapters_index.search.side_effect = fake_search

    result = service.advanced_search(
        [
            {"field": "content", "mode": "fuzzy", "value": "铃铛"},
            {"field": "content", "mode": "fuzzy", "value": "仙"},
        ],
        match="and",
        scope="chapters",
    )

    native_call = chapters_index.search.call_args_list[0]
    options = native_call.args[1]
    assert native_call.args[0] == "铃铛 仙"
    assert options["matchingStrategy"] == "all"
    assert "content" not in options["attributesToRetrieve"]
    assert result["total"] == 9
    assert result["hits"][0]["matched_fields"] == ["content", "content"]


def test_fuzzy_content_and_author_is_served_natively_full_index():
    """One fuzzy 正文 condition AND-ed with author is one native query + filter.

    ``正文 铃铛 AND 作者 吴承恩`` must reach the whole index instead of the
    10 000-candidate window, by asking the engine for ``铃铛`` (all terms)
    restricted to the author's gram filter.
    """
    service, _, chapters_index = _service_with_indexes()
    service._mark_gram_ready("chapters")

    def fake_search(query, options):
        if options.get("attributesToCrop"):
            return {"hits": [
                {"id": "c1", "title": "第1章 小铃铛", "book_title": "西游记",
                 "_formatted": {"content": "…村口挂着一只小铃铛…"}},
            ]}
        return {
            "hits": [
                {"id": "c1", "book_id": "b1", "title": "第1章 小铃铛",
                 "book_title": "西游记", "_rankingScore": 0.7},
            ],
            "estimatedTotalHits": 5,
        }

    chapters_index.search.side_effect = fake_search

    result = service.advanced_search(
        [
            {"field": "content", "mode": "fuzzy", "value": "铃铛"},
            {"field": "author", "mode": "fuzzy", "value": "吴承恩"},
        ],
        match="and",
        scope="chapters",
    )

    native_call = chapters_index.search.call_args_list[0]
    options = native_call.args[1]
    assert native_call.args[0] == "铃铛"
    assert options["matchingStrategy"] == "all"
    assert "content" not in options["attributesToRetrieve"]
    assert options["filter"] and "book_author_grams" in options["filter"]
    assert result["total"] == 5
    assert result["hits"][0]["matched_fields"] == ["content", "author"]


def test_deep_page_of_a_single_condition_reuses_the_scanned_ranking():
    service, books_index, _ = _service_with_indexes()
    scan_hits = [
        {"id": f"b{i}", "title": "小铃铛", "_rankingScore": (500 - i) / 500}
        for i in range(500)
    ]
    books_index.search.side_effect = lambda query, options: (
        {"hits": [{"id": "b250", "title": "小铃铛"}]}
        if options.get("filter") else {"hits": scan_hits}
    )

    result = service.advanced_search(
        [{"field": "title", "mode": "fuzzy", "value": "铃铛"}],
        scope="books",
        offset=250,
        limit=40,
    )

    scans = [call for call in books_index.search.call_args_list
             if not call.args[1].get("filter")]
    assert len(scans) == 1, "page 7 must come from the cached ranking"
    assert scans[0].args[1]["limit"] == SearchService.METADATA_CANDIDATE_LIMIT
    assert result["total"] == 500
    assert result["hits"][0]["id"] == "b250"


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

    assert result["total"] == 3
    # 白龙精/白毛鼠 survive only through the second condition: neither carries
    # all of 白骨精.
    assert result["hits"][0]["title"] == "三打白骨精"
    assert {hit["title"] for hit in result["hits"]} == {"三打白骨精", "白龙精", "白毛鼠"}


def test_multi_condition_book_window_covers_every_match():
    """The books window is a correctness knob, not a speed one.

    ``category = 言情`` matches 1 847 books on the live index, so the old flat
    1 000-candidate window made ``title ~ X AND category = 言情`` return 0 for
    books that carry both -- and every OR/AND whose partner ranked below the cut
    behaved the same way ("多条件搜索全是 0 条").
    """
    service, books_index, chapters_index = _service_with_indexes()
    assert service._candidate_window("books", "category_names") == \
        SearchService.METADATA_CANDIDATE_LIMIT
    assert service._candidate_window("books", "title") == \
        SearchService.METADATA_CANDIDATE_LIMIT
    # Chapters are two orders of magnitude slower at that width, and ``content``
    # candidates have to carry the body.
    assert service._candidate_window("chapters", "book_title") == \
        SearchService.CANDIDATE_LIMIT
    assert service._candidate_window("chapters", "content") == \
        SearchService.CONTENT_CANDIDATE_LIMIT

    books_index.search.return_value = {"hits": []}
    chapters_index.search.return_value = {"hits": []}

    service.advanced_search(
        [
            {"field": "title", "mode": "exact", "value": "晴晴的"},
            {"field": "category", "mode": "exact", "value": "言情"},
        ],
        match="and",
        scope="all",
    )

    windows = [
        call.args[1]["limit"] for call in books_index.search.call_args_list
    ]
    assert windows == [SearchService.METADATA_CANDIDATE_LIMIT,
                       SearchService.METADATA_CANDIDATE_LIMIT]


def test_multi_condition_and_keeps_a_book_the_wide_window_reaches():
    service, books_index, chapters_index = _service_with_indexes()
    # "晴晴的乖巧日记" ranks 1 500th for 言情: outside the old 1 000 window.
    ranked_after_cut = [
        {"id": f"other-{i}", "title": f"别的书{i}", "author": "x",
         "tags": [], "category_names": ["言情"], "is_r18": False}
        for i in range(1200)
    ]
    target = {"id": "target", "title": "晴晴的乖巧日记", "author": "哈基米",
              "tags": [], "category_names": ["言情"], "is_r18": False}
    books_index.search.side_effect = lambda query, options: {
        "hits": [target] if "晴晴" in str(query) else ranked_after_cut + [target]
    }
    chapters_index.search.return_value = {"hits": []}

    result = service.advanced_search(
        [
            {"field": "title", "mode": "exact", "value": "晴晴的"},
            {"field": "category", "mode": "exact", "value": "言情"},
        ],
        match="and",
        scope="all",
    )

    assert result["total"] == 1
    assert result["hits"][0]["title"] == "晴晴的乖巧日记"


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


# ---- same-field AND: one conjunction query instead of two detached windows ----

def _conjunction_condition():
    return [
        {"field": "content", "mode": "exact", "value": "铃"},
        {"field": "content", "mode": "exact", "value": "仙"},
    ]


def _chapter_hits():
    return [
        {"id": "c1", "book_id": "b1", "title": "第1章", "book_title": "铃仙传"},
        {"id": "c2", "book_id": "b2", "title": "第2章", "book_title": "仙铃录"},
    ]


def _chapter_bodies():
    return {"c1": "铃儿走进仙山。", "c2": "仙子摇响了铃。"}


def test_single_content_scan_uses_all_matching_for_multichar_candidates():
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.return_value = {
        "hits": [
            {"id": f"c{index_number}", "content": "师妹的乳环"}
            for index_number in range(SearchService.CONTENT_RECALL_RETRY_THRESHOLD)
        ],
    }

    ranked = service._scan_condition(
        SearchService.INDEX_CHAPTERS,
        "content",
        "乳环",
        None,
        SearchService.CONTENT_CANDIDATE_LIMIT,
    )

    assert len(ranked) == SearchService.CONTENT_RECALL_RETRY_THRESHOLD
    assert chapters_index.search.call_count == 1
    assert chapters_index.search.call_args.args[0] == "乳环"
    assert chapters_index.search.call_args.args[1]["matchingStrategy"] == "all"


def test_single_content_scan_unions_legacy_candidates_when_underfilled():
    service, _, chapters_index = _service_with_indexes()

    def _search(query, options):
        if options.get("matchingStrategy") == "all":
            return {"hits": [{"id": "c1", "content": "师妹的乳环"}]}
        return {"hits": [
            {"id": "c1", "content": "师妹的乳环"},
            {"id": "c2", "content": "另一本书也有乳环"},
        ]}

    chapters_index.search.side_effect = _search

    ranked = service._scan_condition(
        SearchService.INDEX_CHAPTERS,
        "content",
        "乳环",
        None,
        SearchService.CONTENT_CANDIDATE_LIMIT,
    )

    assert [row[1] for row in ranked] == ["c1", "c2"]
    assert chapters_index.search.call_count == 2
    assert chapters_index.search.call_args_list[0].args[1]["matchingStrategy"] == "all"
    assert "matchingStrategy" not in chapters_index.search.call_args_list[1].args[1]


def _route_chapters(conjunction_hits, stored, engine_total):
    """Route the mock's calls the way the engine answers them.

    The conjunction query carries the terms; the page hydration (cropped around
    the match) and the verification fetch are the two ``id IN [...]`` calls that
    follow it.
    """
    by_id = {hit["id"]: hit for hit in conjunction_hits}

    def _search(query, options):
        if options.get("matchingStrategy"):
            return {"hits": [{"id": hit["id"]} for hit in conjunction_hits],
                    "estimatedTotalHits": engine_total}
        asked = [
            doc_id for doc_id in stored
            if f'"{doc_id}"' in str(options.get("filter"))
        ]
        attrs = options.get("attributesToRetrieve") or []
        if "content" in attrs:
            return {"hits": [{"id": doc_id, "content": stored[doc_id]} for doc_id in asked]}
        return {"hits": [by_id[doc_id] for doc_id in asked if doc_id in by_id]}

    return _search


def test_same_field_and_asks_the_engine_for_the_intersection():
    """正文 铃 AND 正文 仙 returned 0: two detached windows never overlap.

    正文 「铃」 alone matches 9 619 chapters and 「仙」 matches 10 000+, but their
    top-300 relevance windows barely overlap, so the Python intersection was
    empty while 1 299 chapters really carry both.  The engine can express that
    AND (``matchingStrategy: "all"``), so it does the selection now.
    """
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.side_effect = _route_chapters(
        _chapter_hits(), _chapter_bodies(), 1299,
    )

    result = service.advanced_search(
        _conjunction_condition(), match="and", scope="all", limit=40,
    )

    scan = chapters_index.search.call_args_list[0]
    assert scan.args[0] == "铃 仙"
    assert scan.args[1]["attributesToSearchOn"] == ["content"]
    assert scan.args[1]["matchingStrategy"] == "all"
    assert result["total"] == 1299, "the engine's count is the honest total"
    assert [hit["id"] for hit in result["hits"]] == ["c1", "c2"]
    assert result["hits"][0]["type"] == "chapter"
    assert result["hits"][0]["matched_fields"] == ["content", "content"]
    assert result["hits"][0]["score"] == 2


def test_same_field_and_page_rechecks_the_real_text():
    """Fuzzy conjunctions still scatter-match, so the page is re-verified.

    The quoted phrase query only applies to exact values: a fuzzy condition
    keeps its bare character tokens, and a chapter holding one fuzzy character
    but not the substring must not be served.  (Exact values need no such
    gate -- the engine already required the contiguous phrase.)
    """
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.side_effect = [
        {"hits": [{"id": "c1"}, {"id": "c2"}], "estimatedTotalHits": 2},
        {"hits": []},
        {"hits": [
            {"id": "c1", "content": "小镇上的铃铛，仙人来了"},
            {"id": "c2", "content": "铛的一声，仙子来了"},
        ]},
    ]

    result = service.advanced_search(
        [
            {"field": "content", "mode": "fuzzy", "value": "铃铛"},
            {"field": "content", "mode": "exact", "value": "仙"},
        ],
        match="and",
        scope="chapters",
    )

    # c2 has 铛 and 仙 but never 铃+铛, so the fuzzy gate drops it.
    assert [hit["id"] for hit in result["hits"]] == ["c1"]


def test_same_field_and_keeps_engine_hits_when_the_text_is_unavailable():
    """Verification needs the stored field; a failed fetch must not empty a page."""
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.side_effect = [
        {"hits": [{"id": "c1"}], "estimatedTotalHits": 1},
        {"hits": []},
        {"hits": []},
    ]

    result = service.advanced_search(
        _conjunction_condition(), match="and", scope="chapters",
    )

    assert result["total"] == 1
    assert [hit["id"] for hit in result["hits"]] == ["c1"]


def test_same_field_and_serves_a_snippet_anchored_at_the_match():
    """The engine crops *around* the terms, so the hit sits mid-window.

    A centred crop puts the searched word ~70 characters in (measured live: 铃
    at offset 73 of a 118-character crop), which the two-line clamp hides on a
    phone while the desktop still shows it.  The served excerpt is re-anchored.
    """
    service, _, chapters_index = _service_with_indexes()
    crop = "甲" * 70 + "铃" + "乙" * 40 + "仙" + "丙" * 40

    def _search(query, options):
        if options.get("matchingStrategy"):
            return {"hits": [{"id": "c1"}], "estimatedTotalHits": 1}
        if "content" in (options.get("attributesToRetrieve") or []):
            return {"hits": [{"id": "c1", "content": crop}]}
        return {"hits": [{
            "id": "c1", "book_id": "b1", "title": "第1章", "book_title": "铃仙传",
            "_formatted": {"content": crop},
        }]}

    chapters_index.search.side_effect = _search

    result = service.advanced_search(
        _conjunction_condition(), match="and", scope="chapters",
    )

    snippet = result["hits"][0]["snippet"]
    assert "铃" in snippet and "仙" in snippet
    assert snippet.index("铃") <= SearchService.SNIPPET_LEAD_CHARS + len("...")


def test_same_field_and_reuses_the_ranking_for_later_pages():
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.side_effect = _route_chapters(
        _chapter_hits(), _chapter_bodies(), 2,
    )

    first = service.advanced_search(
        _conjunction_condition(), match="and", scope="chapters", offset=0, limit=1,
    )
    second = service.advanced_search(
        _conjunction_condition(), match="and", scope="chapters", offset=1, limit=1,
    )

    scans = [
        call for call in chapters_index.search.call_args_list
        if call.args[1].get("matchingStrategy")
    ]
    assert len(scans) == 1, "page 2 must come from the cached conjunction ranking"
    assert first["hits"][0]["id"] == "c1"
    assert second["hits"][0]["id"] == "c2"
    assert first["total"] == second["total"] == 2


def test_same_field_and_or_match_keeps_the_per_condition_path():
    """OR is a union, not an intersection: it must not go through the conjunction."""
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.return_value = {"hits": []}

    result = service.advanced_search(
        _conjunction_condition(), match="or", scope="chapters",
    )

    queries = [call.args[0] for call in chapters_index.search.call_args_list]
    assert "铃 仙" not in queries
    assert result["total"] == 0


def test_book_metadata_and_keeps_the_wide_window_path():
    """Books metadata ANDs already reach every match through the 10 000 window."""
    service, books_index, chapters_index = _service_with_indexes()
    books_index.search.return_value = {"hits": []}
    chapters_index.search.return_value = {"hits": []}

    service.advanced_search(
        [
            {"field": "title", "mode": "exact", "value": "晴晴的"},
            {"field": "title", "mode": "exact", "value": "日记"},
        ],
        match="and",
        scope="books",
    )

    queries = [call.args[0] for call in books_index.search.call_args_list]
    assert queries == ["晴晴的", "日记"]
    assert all(
        "matchingStrategy" not in call.args[1]
        for call in books_index.search.call_args_list
    )


def test_conjunction_query_quotes_exact_multi_character_values():
    """Quote exact values to improve engine ranking before body verification."""
    assert (
        SearchService._conjunction_query([
            {"field": "content", "mode": "exact", "value": "师妹"},
            {"field": "content", "mode": "exact", "value": "乳环"},
        ])
        == '"师妹" "乳环"'
    )


def test_conjunction_query_leaves_fuzzy_and_single_char_values_bare():
    """Fuzzy values keep bare tokens: fuzzy only needs every char present."""
    assert (
        SearchService._conjunction_query([
            {"field": "content", "mode": "fuzzy", "value": "铃铛"},
            {"field": "content", "mode": "exact", "value": "仙"},
        ])
        == "铃铛 仙"
    )
    assert (
        SearchService._conjunction_query([
            {"field": "content", "mode": "exact", "value": "铃"},
            {"field": "content", "mode": "exact", "value": "仙"},
        ])
        == "铃 仙"
    )


def test_same_field_exact_and_filters_cjk_false_positives():
    """Quoted CJK queries can still return scattered-character candidates."""
    service, _, chapters_index = _service_with_indexes()

    def _search(query, options):
        if options.get("matchingStrategy") == "all":
            return {"hits": [{"id": "c1"}, {"id": "c2"}], "estimatedTotalHits": 2}
        if "content" in (options.get("attributesToRetrieve") or []):
            return {"hits": [
                {"id": "c1", "content": "师妹与乳环同时出现"},
                {"id": "c2", "content": "师妹出现，但乳与环分开"},
            ]}
        return {"hits": [
            {"id": "c1", "book_id": "b1", "title": "第1章", "book_title": "师妹传",
             "_formatted": {"content": "师妹…乳环"}},
            {"id": "c2", "book_id": "b2", "title": "第2章", "book_title": "乳环录",
             "_formatted": {"content": "乳环…师妹"}},
        ]}

    chapters_index.search.side_effect = _search
    result = service.advanced_search(
        [
            {"field": "content", "mode": "exact", "value": "师妹"},
            {"field": "content", "mode": "exact", "value": "乳环"},
        ],
        match="and",
        scope="chapters",
    )

    assert [hit["id"] for hit in result["hits"]] == ["c1"]
    assert result["total"] == 1
    scan = chapters_index.search.call_args_list[0]
    assert scan.args[0] == '"师妹" "乳环"'
    assert scan.args[1]["matchingStrategy"] == "all"


# ---- derived gram fields: exact CJK substring matching at engine depth ----
#
# The behaviour under test is the fix for "search has a number limit": a metadata
# search could only ever see the candidates the engine ranked inside its window,
# so a query matching more documents than that window reported a smaller total
# and could not be paged further.  These tests pin the two halves -- that every
# write path produces the fields, and that a search only takes the gram path once
# a backfill has verified the whole index carries them.


def _gram_ready_books_service(fake_search):
    service, books_index, _ = _service_with_indexes()
    service._mark_gram_ready(SearchService.INDEX_BOOKS)
    books_index.search.side_effect = fake_search
    return service, books_index


def test_index_book_writes_the_gram_fields():
    service, client = _make_service()
    index = client.index.return_value

    service.index_book({"id": "b1", "title": "【小铃铛】", "author": "张三"})

    doc = index.add_documents.call_args.args[0][0]
    assert doc["title_grams"] == field_grams("【小铃铛】")
    assert "铃铛" in doc["title_grams"]  # the query this whole path exists for
    assert doc["author_grams"] == field_grams("张三")
    assert doc["kind"]  # the pre-existing field is still written


def test_index_chapter_writes_the_gram_fields():
    service, client = _make_service()
    index = client.index.return_value

    service.index_chapter(
        {"id": "c1", "title": "第1章", "book_title": "铃铛", "book_author": "张三"}
    )

    doc = index.add_documents.call_args.args[0][0]
    assert doc["book_title_grams"] == field_grams("铃铛")
    assert doc["book_author_grams"] == field_grams("张三")
    assert doc["title_grams"] == field_grams("第1章")


def test_buffered_chapters_carry_their_gram_fields():
    """``flush_chapters`` replaces the whole document, so grams must be buffered."""
    service, client = _make_service()
    index = client.index.return_value

    service.buffer_chapter({"id": "c1", "book_title": "铃铛"})
    service.flush_chapters()

    doc = index.add_documents.call_args.args[0][0]
    assert doc["book_title_grams"] == field_grams("铃铛")


def test_update_book_tags_merges_instead_of_replacing_the_document():
    """``add_documents`` is HTTP POST (replace); a tags-only write wiped the doc.

    It dropped ``kind`` (so the book vanished from /novels and /comics search)
    and every other field with it, and only a later ``sync_book_kinds`` backfill
    repaired the damage.  ``update_documents`` is HTTP PUT (merge).
    """
    service, client = _make_service()
    index = client.index.return_value

    service.update_book_tags("b1", ["武侠"])
    service.update_chapter_tags("c1", ["武侠"])

    assert index.add_documents.call_count == 0
    assert index.update_documents.call_count == 2
    assert index.update_documents.call_args_list[0].args[0] == [
        {"id": "b1", "tags": ["武侠"]}
    ]


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Async-session stand-in that records the statements it is asked to run.

    ``rows`` may be a list or a callable taking the statement, so one fake can
    answer the different queries a backfill issues.
    """

    def __init__(self, captured: list, rows=None):
        self.captured = captured
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, statement):
        self.captured.append(statement)
        rows = self.rows(statement) if callable(self.rows) else self.rows
        return _FakeResult(rows)


def test_missing_document_ids_pages_until_the_engine_runs_short():
    """A ``NOT EXISTS`` sweep collects ids, not the library it lives in."""
    service, client = _make_service()
    index = client.index.return_value

    def engine(query, options):
        offset = int(options.get("offset") or 0)
        limit = int(options["limit"])
        remaining = 2500 - offset
        count = max(0, min(limit, remaining))
        return {"hits": [{"id": f"b{offset + i}"} for i in range(count)]}

    index.search.side_effect = engine

    ids = service.missing_document_ids("books", "kind NOT EXISTS")

    assert len(ids) == 2500
    assert ids[0] == "b0" and ids[-1] == "b2499"
    assert index.search.call_args_list[0].args[1]["filter"] == "kind NOT EXISTS"
    assert index.search.call_args_list[0].args[1]["limit"] == (
        SearchService.MISSING_SCAN_CHUNK
    )


@pytest.mark.asyncio
async def test_startup_kind_repair_touches_only_the_missing_documents():
    """The startup repair must not become a full-library rewrite.

    The probe reports "something is missing" on the production index every single
    time, and acting on it by rewriting the library meant 718k chapter updates
    per restart (a near-full re-index) which could not change anything.  Only the
    ids the engine actually reported may be written.
    """
    captured: list = []
    service, client = _make_service()
    books_index = MagicMock()
    chapters_index = MagicMock()
    client.index.side_effect = lambda name: (
        books_index if name == "books" else chapters_index
    )

    books_index.search.return_value = {"hits": [{"id": "b1"}, {"id": "b2"}]}
    chapters_index.search.return_value = {"hits": []}

    def rows_for(statement):
        sql = str(statement)
        if "books.kind" in sql:  # the repair query
            return [("b1", "novel"), ("b2", "comic")]
        return [("b1",), ("b2",)]  # the presence check

    with patch.object(
        settings, "MEILI_GRAM_BACKFILL_CHAPTERS", False
    ), patch(
        "app.core.database.SessionLocal",
        lambda: _FakeSession(captured, rows_for),
    ):
        result = await service.sync_book_kinds()

    assert result["books"] == 2
    assert result["chapters"] == 0
    assert result["orphans"] == 0
    # Exactly the two reported documents, in one request.
    books_index.update_documents.assert_called_once()
    written = books_index.update_documents.call_args.args[0]
    assert sorted(doc["id"] for doc in written) == ["b1", "b2"]
    # No library walk: the statements are the presence check and the repair only.
    assert len(captured) == 2
    chapters_index.update_documents.assert_not_called()


@pytest.mark.asyncio
async def test_startup_kind_repair_drops_orphans_so_the_probe_can_ever_pass():
    """A document whose row is gone can never be repaired -- delete it.

    Leaving orphans in place is what kept ``kind NOT EXISTS`` matching on every
    startup: the probe could never report the index as complete, so the repair
    ran (as a full rewrite) on every restart, forever.
    """
    service, client = _make_service()
    books_index = MagicMock()
    chapters_index = MagicMock()
    client.index.side_effect = lambda name: (
        books_index if name == "books" else chapters_index
    )
    books_index.search.return_value = {"hits": [{"id": "gone"}]}
    chapters_index.search.return_value = {"hits": []}

    with patch(
        "app.core.database.SessionLocal",
        lambda: _FakeSession([], []),  # the presence check finds no row
    ):
        result = await service.sync_book_kinds()

    assert result["orphans"] == 1
    assert result["books"] == 0
    books_index.delete_documents.assert_called_once_with(["gone"])
    books_index.update_documents.assert_not_called()


@pytest.mark.asyncio
async def test_gram_backfill_skips_chapters_unless_they_are_enabled():
    """Chapters cost a near-full re-index, so they are opt-in."""
    service, client = _make_service()
    index = client.index.return_value
    index.search.return_value = {"hits": []}

    with patch.object(settings, "MEILI_GRAM_BACKFILL_CHAPTERS", False):
        result = await service.sync_book_grams()

    assert result["enabled"] == [SearchService.INDEX_BOOKS]
    assert not service.gram_index_ready(SearchService.INDEX_CHAPTERS)


def test_gram_filter_expression_covers_every_gram_field_of_an_index():
    assert SearchService.gram_missing_filter("books") == (
        "title_grams NOT EXISTS OR author_grams NOT EXISTS"
    )
    assert SearchService.gram_missing_filter("chapters") == (
        "title_grams NOT EXISTS OR book_title_grams NOT EXISTS "
        "OR book_author_grams NOT EXISTS"
    )


@pytest.mark.asyncio
async def test_gram_backfill_statements_are_executable():
    """The production backfill died on its first statement -- pin that it can run.

    ``Book.author_name`` is a Python property over the ``author`` relationship,
    not a mapped column (``models/book.py``).  Naming it in a ``select()`` raises
    "Column expression, FROM clause, or other columns clause element expected",
    which is how the startup backfill died on 2026-10-14 and left the gram path
    off for a day.  Compiling every statement the backfill builds catches that
    class of mistake without a database, and the joins are pinned too.
    """
    from app.core import database as database_module

    captured: list = []
    service, client = _make_service()
    # "missing" so the backfill actually builds its queries, and chapters on so
    # both index walks are exercised (they are opt-in by default).
    client.index.return_value.search.return_value = {"hits": [{"id": "x"}]}

    with patch.object(
        database_module, "SessionLocal", lambda: _FakeSession(captured)
    ), patch.object(settings, "MEILI_GRAM_BACKFILL_CHAPTERS", True):
        await service.sync_book_grams()

    assert captured, "the backfill built no statements at all"
    compiled = [str(statement.compile()) for statement in captured]

    walk = [sql for sql in compiled if "authors" in sql]
    assert walk, "no statement joins the authors table"
    # A missing author must not drop the row: every walk left-joins it, so each
    # document receives its own grams and the completeness probe can pass.
    assert all("LEFT OUTER JOIN" in sql.upper() for sql in walk)
    # The property must never reach a select list.
    assert "author_name" not in " ".join(compiled)
    assert any("chapters" in sql for sql in compiled)
    assert any("books" in sql for sql in compiled)


def test_gram_probe_finds_an_index_that_still_lacks_the_fields():
    service, client = _make_service()
    index = client.index.return_value
    index.search.return_value = {"hits": [{"id": "b1"}]}

    assert service.index_missing_grams(SearchService.INDEX_BOOKS) is True

    options = index.search.call_args.args[1]
    # Every gram field is probed, not just the first: an index carrying
    # title_grams but not author_grams would pass a single-field probe and then
    # serve an author search that hides documents.
    assert options["filter"] == (
        "title_grams NOT EXISTS OR author_grams NOT EXISTS"
    )
    assert options["limit"] == 1


def test_gram_probe_treats_an_unprobeable_index_as_missing():
    """No answer must never be read as "safe to narrow".

    Taking the gram path on an index whose state could not be established would
    answer every metadata search with nothing.
    """
    service, client = _make_service()
    client.index.return_value.search.side_effect = RuntimeError("engine down")

    assert service.index_missing_grams(SearchService.INDEX_BOOKS) is True


@pytest.mark.asyncio
async def test_gram_backfill_marks_an_already_complete_index_ready():
    service, client = _make_service()
    index = client.index.return_value
    index.search.return_value = {"hits": []}  # nothing missing

    result = await service.sync_book_grams()

    assert result["skipped"] is True
    assert (result["books"], result["chapters"], result["orphans"]) == (0, 0, 0)
    # Chapters are opt-in: gramming them re-tokenises every chapter body, so the
    # default is the cheap index only.
    assert result["enabled"] == [SearchService.INDEX_BOOKS]
    assert index.update_documents.call_count == 0
    assert service.gram_index_ready(SearchService.INDEX_BOOKS)
    # The index that was not backfilled must never be reported ready, or searches
    # would filter on fields it does not carry and answer with nothing.
    assert not service.gram_index_ready(SearchService.INDEX_CHAPTERS)


def test_metadata_search_keeps_its_window_until_the_index_is_verified():
    service, books_index, _ = _service_with_indexes()
    books_index.search.return_value = {"hits": []}

    service.advanced_search(
        [{"field": "title", "mode": "exact", "value": "白骨精"}], scope="books"
    )

    scan = books_index.search.call_args_list[0].args[1]
    assert "title_grams" not in (scan.get("filter") or "")
    assert scan["limit"] == SearchService.METADATA_CANDIDATE_LIMIT


def test_verified_index_narrows_the_metadata_scan_with_a_gram_filter():
    hits = [{"id": f"b{i}", "title": "三打白骨精", "_rankingScore": 0.5} for i in range(3)]
    service, books_index = _gram_ready_books_service(
        lambda query, options: (
            {"hits": []} if "id IN" in (options.get("filter") or "") else {"hits": hits}
        )
    )

    result = service.advanced_search(
        [{"field": "title", "mode": "exact", "value": "白骨精"}], scope="books"
    )

    scan = books_index.search.call_args_list[0].args[1]
    assert scan["filter"] == '(title_grams = "白骨") AND (title_grams = "骨精")'
    # Narrowed walks are chunked: the engine has already discarded the
    # non-matches, so there is no wide ranking window to fetch in one request.
    assert scan["limit"] == SearchService.METADATA_SCAN_CHUNK
    assert result["total"] == 3


def test_gram_narrowed_scan_reaches_the_hits_the_wide_window_could_not():
    """The reported limit at unit scale: 12 466 real matches, 10 000 served.

    The ungrammed scan asks the engine for its whole 10 000-wide candidate window
    in one request, so everything ranked below that is invisible and the total is
    an undercount.  The gram-filtered scan ranks only the documents that actually
    hold the query and walks them until a chunk comes back short.
    """
    total_hits = 12_466
    docs = [
        {"id": f"b{i:05d}", "title": "铃铛", "_rankingScore": 0.5}
        for i in range(total_hits)
    ]

    def engine(query, options):
        if "id IN" in (options.get("filter") or ""):
            return {"hits": []}  # hydration: the total does not depend on it
        offset = int(options.get("offset") or 0)
        limit = int(options.get("limit") or 0)
        return {"hits": docs[offset : offset + limit]}

    windowed, windowed_index, _ = _service_with_indexes()
    windowed_index.search.side_effect = engine
    before = windowed.advanced_search(
        [{"field": "title", "mode": "exact", "value": "铃铛"}], scope="books"
    )

    narrowed, _ = _gram_ready_books_service(engine)
    after = narrowed.advanced_search(
        [{"field": "title", "mode": "exact", "value": "铃铛"}], scope="books"
    )

    assert before["total"] == SearchService.METADATA_CANDIDATE_LIMIT
    assert after["total"] == total_hits
    # And the extra hits are pageable, not just counted.
    last_page = narrowed.advanced_search(
        [{"field": "title", "mode": "exact", "value": "铃铛"}],
        scope="books",
        offset=total_hits - 40,
        limit=40,
    )
    assert len(last_page["hits"]) == 40


def test_fuzzy_search_is_narrowed_by_its_characters_too():
    """The library search box sends ``fuzzy`` for title/author.

    Fuzzy means "every character appears somewhere, any order, gaps allowed", so
    an AND over the query's distinct characters is exactly that test -- narrowing
    only ``exact`` would have left the most-used search on its window.
    """
    service, books_index = _gram_ready_books_service(
        lambda query, options: {"hits": []}
    )

    service.advanced_search(
        [{"field": "title", "mode": "fuzzy", "value": "铃铛"}], scope="books"
    )

    fuzzy_scan = books_index.search.call_args_list[0].args[1]
    assert fuzzy_scan["filter"] == '(title_grams = "铃") AND (title_grams = "铛")'
    assert fuzzy_scan["limit"] == SearchService.METADATA_SCAN_CHUNK


def test_single_character_exact_search_is_narrowed_by_that_character():
    """The broadest exact query there is, and the one most needing a filter.

    的 matches 12 466 books on the live index -- well past the 10 000-candidate
    window a single-character search used to fall back to.
    """
    service, books_index = _gram_ready_books_service(
        lambda query, options: {"hits": []}
    )

    service.advanced_search(
        [{"field": "title", "mode": "exact", "value": "铃"}], scope="books"
    )

    scan = books_index.search.call_args_list[0].args[1]
    assert scan["filter"] == 'title_grams = "铃"'
    assert scan["limit"] == SearchService.METADATA_SCAN_CHUNK


def test_ungrammed_fields_keep_their_window_in_both_modes():
    """``description`` is deliberate: 2 000 characters of grams per book is not.

    Whatever the mode, a field with no gram attribute must fall back rather than
    be filtered on a field that does not exist.
    """
    service, books_index = _gram_ready_books_service(
        lambda query, options: {"hits": []}
    )

    for mode in ("exact", "fuzzy"):
        books_index.search.reset_mock()
        service.advanced_search(
            [{"field": "description", "mode": mode, "value": "铃铛"}], scope="books"
        )
        scan = books_index.search.call_args_list[0].args[1]
        assert "grams" not in (scan.get("filter") or "")
        assert scan["limit"] == SearchService.METADATA_CANDIDATE_LIMIT


def test_page_cache_is_bounded_by_rows_not_only_by_entries():
    """A cached ranking can now cover a whole index, so entry count is not a bound.

    Sixteen entries of a few hundred thousand rows each would be hundreds of MB
    in a backend container limited to 1 GB, which is what raising a scan budget
    would otherwise have bought silently.
    """
    service, _ = _make_service()
    budget = SearchService.PAGE_CACHE_MAX_ROWS
    big = [(1, f"b{i}", "") for i in range(budget // 2 + 1)]

    service._page_cache_put("first", big)
    service._page_cache_put("second", big)

    assert SearchService._cached_rows(service._page_cache) <= budget
    assert "second" in service._page_cache  # the newest entry always survives
    assert "first" not in service._page_cache


def test_page_cache_keeps_an_entry_larger_than_the_whole_budget():
    """The result just computed must be servable from cache, not evicted by itself."""
    service, _ = _make_service()
    oversized = [(1, f"b{i}", "") for i in range(SearchService.PAGE_CACHE_MAX_ROWS + 10)]

    service._page_cache_put("only", oversized)

    assert service._page_cache_get("only") is oversized


def test_condition_cache_is_bounded_by_rows_too():
    service, _ = _make_service()
    budget = SearchService.CONDITION_CACHE_MAX_ROWS
    candidates = {f"b{i}": (1, {"id": f"b{i}"}) for i in range(budget // 2 + 1)}

    service._condition_cache["a"] = (time.monotonic() + 60, candidates)
    service._condition_cache["b"] = (time.monotonic() + 60, candidates)
    service._condition_cache["c"] = (time.monotonic() + 60, candidates)

    service._evict_to_budget(
        service._condition_cache,
        SearchService.CONDITION_CACHE_MAX_ENTRIES,
        budget,
    )

    assert SearchService._cached_rows(service._condition_cache) <= budget
    assert "c" in service._condition_cache


def test_scan_budget_is_per_index_and_never_exceeds_the_engine_ceiling():
    """A gram-narrowed walk is bounded by the index it walks.

    The chapters index is orders of magnitude more expensive per row in
    production -- 666k documents in a 94 GB store on 12 GB of RAM, where one
    unfiltered 10 000-row id-only window measured 514-631 s (codex-handoff
    section 51) -- so it must not inherit the books index's budget just because
    books can afford it.
    """
    assert (
        SearchService.scan_budget(SearchService.INDEX_BOOKS)
        == SearchService.MAX_TOTAL_HITS
    )
    assert (
        SearchService.scan_budget(SearchService.INDEX_CHAPTERS)
        == SearchService.SCAN_BUDGET_CHAPTERS
    )
    assert SearchService.SCAN_BUDGET_CHAPTERS < SearchService.SCAN_BUDGET_BOOKS

    # Asking for more rows than the engine will serve is not an error, it just
    # stops there -- and a budget above the ceiling would report a total the
    # engine cannot page.
    with patch.object(SearchService, "MAX_TOTAL_HITS", 5_000):
        assert SearchService.scan_budget(SearchService.INDEX_BOOKS) == 5_000
        assert SearchService.scan_budget(SearchService.INDEX_CHAPTERS) == 5_000


def test_chapter_gram_walk_stops_at_the_chapter_budget():
    """A chapter walk cannot run away just because the filter is narrowing.

    The fake engine never returns a short chunk, so the only thing that can end
    the walk is the budget.  It also pins the *step*: metadata walks in large
    steps because every request re-ranks the whole candidate set, so a walk that
    fetched the same rows in ``_SCAN_CHUNK``-sized pieces would multiply the
    engine's work by the number of steps.
    """
    service, _, chapters_index = _service_with_indexes()
    service._mark_gram_ready(SearchService.INDEX_CHAPTERS)

    def engine(query, options):
        limit = int(options.get("limit") or 0)
        return {
            "hits": [
                {"id": f"c{options.get('offset', 0)}-{i}", "book_title": "铃铛"}
                for i in range(limit)
            ]
        }

    chapters_index.search.side_effect = engine
    # A chapter-scoped ``title`` condition is scored against the chapter's
    # ``book_title``, which is the grammed attribute.
    service._single_condition_search(
        {"field": "title", "mode": "exact", "value": "铃铛"},
        scope="chapters",
        filters=None,
        offset=0,
        limit=40,
    )

    scans = [
        call.args[1] for call in chapters_index.search.call_args_list
        if call.args[1].get("filter", "").startswith("book_title_grams")
    ]
    fetched = sum(int(scan["limit"]) for scan in scans)
    assert fetched == SearchService.SCAN_BUDGET_CHAPTERS
    assert all(
        int(scan["limit"]) == SearchService.METADATA_SCAN_CHUNK for scan in scans
    )
    assert len(scans) == -(-SearchService.SCAN_BUDGET_CHAPTERS
                           // SearchService.METADATA_SCAN_CHUNK)


def test_gram_narrowing_is_anded_with_the_visibility_and_kind_filters():
    service, books_index = _gram_ready_books_service(
        lambda query, options: {"hits": []}
    )

    service.advanced_search(
        [{"field": "title", "mode": "exact", "value": "铃铛"}],
        scope="books",
        kind="novel",
    )

    scan = books_index.search.call_args_list[0].args[1]
    assert scan["filter"] == '(kind = "novel") AND (title_grams = "铃铛")'


def test_chapter_metadata_and_search_gets_the_gram_narrowing_too():
    """The 1 000-candidate chapter window hid 99% of the chapter index.

    A chapter-scoped ``title``/``author`` condition is scored against the
    chapter's ``book_title``/``book_author``, both of which are grammed -- so a
    multi-condition AND on them narrows at the engine instead of intersecting
    two truncated windows.  That is the same class of bug as "多条件搜索全是 0 条",
    on the chapter side, where the window is 1 000 rather than 10 000.
    """
    service, _, chapters_index = _service_with_indexes()
    service._mark_gram_ready(SearchService.INDEX_CHAPTERS)
    chapters_index.search.side_effect = lambda query, options: {"hits": []}

    service.advanced_search(
        [
            {"field": "title", "mode": "exact", "value": "铃铛"},
            {"field": "author", "mode": "exact", "value": "张三"},
        ],
        match="and",
        scope="chapters",
    )

    scans = [call.args[1] for call in chapters_index.search.call_args_list]
    filters = [scan.get("filter") or "" for scan in scans]
    assert any('book_title_grams = "铃铛"' in value for value in filters)
    assert any('book_author_grams = "张三"' in value for value in filters)
    for scan, value in zip(scans, filters):
        if "grams" in value:
            assert scan["limit"] == SearchService.METADATA_SCAN_CHUNK


