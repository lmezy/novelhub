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
    # The list clamps a snippet to two lines and a 320 px phone fits ~21 CJK
    # glyphs per line at ``text-xs``, so the lead plus the marker must stay
    # inside the first line, otherwise mobile hides a hit the desktop shows.
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
    """Exact values must reach the engine as phrase queries.

    Live index: bare ``师妹 乳环`` matched 3 094 chapters (any chapter holding
    the four characters scattered), while only 20 hold both substrings.  The
    page-level gate dropped every scattered hit, so total said thousands and
    the list was empty.  Quoting makes the engine require the substrings.
    """
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


def test_same_field_exact_and_never_drops_a_ranked_page_row():
    """An all-exact AND must serve every row the engine ranked.

    Regression for 师妹 + 乳环: exact values are quoted, so the engine
    already required the substrings; the page gate must not re-drop them,
    otherwise total is non-zero while the list is empty.
    """
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.side_effect = [
        {"hits": [{"id": "c1"}, {"id": "c2"}], "estimatedTotalHits": 2},
        {"hits": [
            {"id": "c1", "book_id": "b1", "title": "第1章", "book_title": "师妹传",
             "_formatted": {"content": "师妹…乳环"}},
            {"id": "c2", "book_id": "b2", "title": "第2章", "book_title": "乳环录",
             "_formatted": {"content": "乳环…师妹"}},
        ]},
        {"hits": [
            {"id": "c1", "content": "这章只有师没有妹，更无乳环二字连写"},
            {"id": "c2", "content": "同样散落的师与妹，以及乳与环"},
        ]},
    ]

    result = service.advanced_search(
        [
            {"field": "content", "mode": "exact", "value": "师妹"},
            {"field": "content", "mode": "exact", "value": "乳环"},
        ],
        match="and",
        scope="chapters",
    )

    # The engine required both phrases; the page keeps both rows.
    assert [hit["id"] for hit in result["hits"]] == ["c1", "c2"]
    assert result["total"] == 2
    scan = chapters_index.search.call_args_list[0]
    assert scan.args[0] == '"师妹" "乳环"'
    assert scan.args[1]["matchingStrategy"] == "all"

