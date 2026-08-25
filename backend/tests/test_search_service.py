from unittest.mock import MagicMock, patch

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


def test_advanced_search_chapters_fuzzy_rank():
    service, _, chapters_index = _service_with_indexes()
    chapters_index.search.return_value = {
        "hits": [
            {
                "id": "c-full",
                "book_id": "b1",
                "title": "三打白骨精",
                "book_title": "西游记",
                "content": "",
                "is_r18": False,
            },
            {
                "id": "c-two",
                "book_id": "b2",
                "title": "白龙精",
                "book_title": "另一本书",
                "content": "",
                "is_r18": False,
            },
            {
                "id": "c-one",
                "book_id": "b3",
                "title": "白毛鼠",
                "book_title": "再一本书",
                "content": "",
                "is_r18": False,
            },
        ]
    }

    result = service.advanced_search(
        [{"field": "chapter_title", "mode": "fuzzy", "value": "白骨精"}],
        match="or",
        scope="chapters",
    )

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
