import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from app.api.routes.yuedu import (
    YueduImportSyncRequest,
    YueduImportRequest,
    _fetch_sources_from_url,
    _make_source_id,
    _normalize_import_url,
    _parse_yckceo_listing_ids,
    import_and_sync_all,
    import_yuedu_sources,
)
from app.services.sync import SyncService


def fake_response(text: str, url: str) -> httpx.Response:
    return httpx.Response(
        200,
        text=text,
        request=httpx.Request("GET", url),
        headers={"content-type": "text/html; charset=utf-8"},
    )


def test_normalize_import_url_accepts_yckceo_and_legado_links():
    assert _normalize_import_url(
        "yuedu://booksource/importonline?src=https%3A%2F%2Fwww.yckceo.com%2Fyuedu%2Fshuyuan%2Fjson%2Fid%2F7630.json"
    ) == "https://www.yckceo.com/yuedu/shuyuan/json/id/7630.json"
    assert _normalize_import_url(
        "legado://import/bookSource?src=https://example.com/a.json"
    ) == "https://example.com/a.json"
    assert _normalize_import_url(
        "https://www.yckceo.com/yuedu/shuyuan/index.html#requestWithoutUA"
    ) == "https://www.yckceo.com/yuedu/shuyuan/index.html"


@pytest.mark.asyncio
async def test_fetch_sources_from_url_passes_no_ua_flag():
    seen_headers: dict | None = None

    async def fake_fetch(url: str, timeout: float = 30.0, headers: dict | None = None):
        nonlocal seen_headers
        seen_headers = headers
        return fake_response(
            json.dumps(
                [{"bookSourceName": "A", "bookSourceUrl": "https://a.com"}]
            ),
            url,
        )

    with patch("app.api.routes.yuedu._fetch_response", side_effect=fake_fetch):
        await _fetch_sources_from_url("https://example.com/a.json#requestWithoutUA")

    assert seen_headers == {"User-Agent": "null"}


def test_parse_yckceo_listing_ids():
    soup = BeautifulSoup(
        """
        <input type="checkbox" class="class_one" name="ids[]" value="7630">
        <input type="checkbox" class="class_one" name="ids[]" value="7657">
        """,
        "lxml",
    )
    assert _parse_yckceo_listing_ids(soup) == ["7630", "7657"]


@pytest.mark.asyncio
async def test_fetch_sources_from_yckceo_listing():
    listing = """
    <html><body>
      <input type="checkbox" class="class_one" name="ids[]" value="7630">
      <input type="checkbox" class="class_one" name="ids[]" value="7657">
    </body></html>
    """
    payload = json.dumps(
        [
            {"bookSourceName": "One", "bookSourceUrl": "https://one.com"},
            {"bookSourceName": "Two", "bookSourceUrl": "https://two.com"},
        ]
    )
    seen: list[str] = []

    async def fake_fetch(url: str, timeout: float = 30.0, headers: dict | None = None):
        seen.append(url)
        if "jsons?id=" in url:
            return fake_response(payload, url)
        return fake_response(listing, url)

    with patch("app.api.routes.yuedu._fetch_response", side_effect=fake_fetch):
        sources = await _fetch_sources_from_url(
            "https://www.yckceo.com/yuedu/shuyuan/index.html"
        )

    assert len(sources) == 2
    assert any("jsons?id=7630-7657" in url for url in seen)


@pytest.mark.asyncio
async def test_fetch_sources_from_yckceo_detail_page():
    source = {"bookSourceName": "One", "bookSourceUrl": "https://one.com"}
    detail_html = (
        '<html><body><input id="jsonurl" value="https://www.yckceo.com/yuedu/shuyuan/json/id/7630.json">'
        f'<pre id="jsonpre">{json.dumps(source)}</pre></body></html>'
    )

    async def fake_fetch(url: str, timeout: float = 30.0, headers: dict | None = None):
        return fake_response(detail_html, url)

    with patch("app.api.routes.yuedu._fetch_response", side_effect=fake_fetch):
        sources = await _fetch_sources_from_url(
            "https://www.yckceo.com/yuedu/shuyuan/content/id/7630.html"
        )

    assert len(sources) == 1
    assert sources[0]["bookSourceName"] == "One"
    assert sources[0]["bookSourceUrl"] == "https://one.com"
    assert sources[0]["ruleSearch"] == {}


@pytest.mark.asyncio
async def test_fetch_sources_from_json_object_with_source_urls():
    async def fake_fetch(url: str, timeout: float = 30.0, headers: dict | None = None):
        if url == "https://repo.example/import":
            return fake_response(
                json.dumps(
                    {
                        "sourceUrls": [
                            "https://repo.example/a.json",
                            "https://repo.example/b.json",
                        ]
                    }
                ),
                url,
            )
        if url.endswith("a.json"):
            return fake_response(
                json.dumps(
                    [{"bookSourceName": "A", "bookSourceUrl": "https://a.com"}]
                ),
                url,
            )
        return fake_response(
            json.dumps(
                [{"bookSourceName": "B", "bookSourceUrl": "https://b.com"}]
            ),
            url,
        )

    with patch("app.api.routes.yuedu._fetch_response", side_effect=fake_fetch):
        sources = await _fetch_sources_from_url("https://repo.example/import")

    assert {source["bookSourceName"] for source in sources} == {"A", "B"}


@pytest.mark.asyncio
async def test_load_sources_from_text_accepts_url():
    from app.api.routes.yuedu import _load_sources_from_text

    async def fake_fetch(url: str, timeout: float = 30.0, headers: dict | None = None):
        return fake_response(
            json.dumps(
                [{"bookSourceName": "A", "bookSourceUrl": "https://a.com"}]
            ),
            url,
        )

    with patch("app.api.routes.yuedu._fetch_response", side_effect=fake_fetch):
        sources = await _load_sources_from_text("https://repo.example/a.json")

    assert len(sources) == 1
    assert sources[0]["bookSourceName"] == "A"


@pytest.mark.asyncio
async def test_import_yuedu_sources_imports_yckceo_listing(mock_db):
    listing = """
    <html><body>
      <input type="checkbox" class="class_one" name="ids[]" value="7630">
      <input type="checkbox" class="class_one" name="ids[]" value="7657">
    </body></html>
    """
    payload = json.dumps(
        [
            {"bookSourceName": "One", "bookSourceUrl": "https://one.com"},
            {"bookSourceName": "Two", "bookSourceUrl": "https://two.com"},
        ]
    )

    async def fake_fetch(url: str, timeout: float = 30.0, headers: dict | None = None):
        if "jsons?id=" in url:
            return fake_response(payload, url)
        return fake_response(listing, url)

    with patch("app.api.routes.yuedu._fetch_response", side_effect=fake_fetch):
        result = await import_yuedu_sources(
            YueduImportRequest(
                url="https://www.yckceo.com/yuedu/shuyuan/index.html"
            ),
            SimpleNamespace(id="u1", role="user"),
            mock_db,
        )

    assert result.total == 2
    assert result.imported == 2
    assert result.updated == 0
    assert mock_db.commit.await_count == 1


@pytest.mark.asyncio
async def test_import_and_sync_all_runs_all_sources(mock_db):
    mock_db.scalar = AsyncMock(return_value=SimpleNamespace())
    source_infos = [
        {"id": "s1", "name": "One"},
        {"id": "s2", "name": "Two"},
    ]
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=mock_db)
    session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "app.api.routes.yuedu.import_yuedu_sources",
            AsyncMock(
                    return_value=SimpleNamespace(
                        total=2,
                        imported=2,
                        skipped=0,
                        updated=0,
                        status="imported",
                        sources=source_infos,
                    )
                ),
        ),
        patch("app.core.database.SessionLocal", return_value=session),
        patch.object(
            SyncService,
            "sync_bookshelf",
            AsyncMock(return_value={"total": 1, "results": []}),
        ),
        patch.object(
            SyncService,
            "discover_and_sync_all",
            AsyncMock(return_value={
                "pages_checked": 1,
                "books_found": 2,
                "books_synced": 1,
                "books_failed": 0,
                "chapters_created": 3,
            }),
        ),
    ):
        result = await import_and_sync_all(
            YueduImportSyncRequest(url="https://repo.example/json", discover=True),
            SimpleNamespace(id="u1", role="user"),
            mock_db,
        )

    assert result.sources_total == 2
    assert len(result.details) == 2
    assert result.books_discovered == 4
    assert result.chapters_downloaded == 6


@pytest.mark.asyncio
async def test_import_yuedu_sources_updates_newer_existing_source(mock_db):
    remote = {
        "bookSourceName": "New",
        "bookSourceUrl": "https://new.com",
        "lastUpdateTime": "200",
    }
    existing = SimpleNamespace(
        id=_make_source_id("New", "https://new.com", "u1"),
        name="Old",
        url="https://old.com",
        config={"lastUpdateTime": "100"},
        is_r18=False,
        owner_id="u1",
        submitter_id=None,
    )
    mock_db.get = AsyncMock(return_value=existing)

    with patch(
        "app.api.routes.yuedu._fetch_sources_from_url",
        AsyncMock(return_value=[remote]),
    ):
        result = await import_yuedu_sources(
            YueduImportRequest(url="https://repo.example/json"),
            SimpleNamespace(id="u1", role="user"),
            mock_db,
        )

    assert result.updated == 1
    assert result.imported == 0
    assert existing.name == "New"
    assert existing.config == remote


def test_legacy_v3_field_names_are_renamed():
    from app.api.routes.yuedu import _normalize_source_config

    source = {
        "bookSourceName": "Legacy V3",
        "bookSourceUrl": "https://legacy.example",
        "enable": True,
        "searchRule": {"bookList": ".list a", "name": "h3@text"},
        "tocRule": {"chapterList": "#list a", "chapterName": "@text"},
        "contentRule": {"content": "#content@html", "replaceRegex": ""},
    }

    normalized = _normalize_source_config(source)

    assert normalized["enabled"] is True
    assert normalized["ruleSearch"] == {"bookList": ".list a", "name": "h3@text"}
    assert normalized["ruleToc"]["chapterList"] == "#list a"
    assert normalized["ruleContent"]["content"] == "#content@html"
    assert "searchRule" not in normalized
    assert "enable" not in normalized


def test_empty_rule_arrays_are_normalized_to_dicts():
    from app.api.routes.yuedu import _normalize_source_config

    source = {
        "bookSourceName": "Empty Rules",
        "bookSourceUrl": "https://empty.example",
        "ruleSearch": [],
        "ruleExplore": [],
        "ruleToc": [],
        "ruleContent": [],
        "ruleBookInfo": {"name": "h1@text"},
    }

    normalized = _normalize_source_config(source)

    assert normalized["ruleSearch"] == {}
    assert normalized["ruleExplore"] == {}
    assert normalized["ruleToc"] == {}
    assert normalized["ruleContent"] == {}
    assert normalized["ruleBookInfo"] == {"name": "h1@text"}


def test_legacy_rule_field_names_are_normalized():
    from app.api.routes.yuedu import _normalize_source_config

    source = {
        "bookSourceName": "Field Norm",
        "bookSourceUrl": "https://norm.example",
        "ruleBookInfo": {
            "name": "h1@text",
            "cover": "img@src",
            "catalogUrl": "a@href",
        },
        "ruleContent": {
            "content": "#content@html",
            "nextContent": "a.next@href",
            "filter": ["\u5e7f\u544a"],
        },
    }

    normalized = _normalize_source_config(source)

    assert normalized["ruleBookInfo"]["coverUrl"] == "img@src"
    assert normalized["ruleBookInfo"]["tocUrl"] == "a@href"
    assert normalized["ruleContent"]["nextContentUrl"] == "a.next@href"
    assert normalized["ruleContent"]["replaceRegex"] == ["\u5e7f\u544a"]


def test_rule_flat_source_is_converted_to_new_format():
    from app.api.routes.yuedu import _normalize_source_config

    source = {
        "bookSourceName": "Old Flat",
        "bookSourceUrl": "https://old.example",
        "ruleSearchUrl": "search?key=searchKey",
        "ruleFindUrl": "category/{{page}}",
        "ruleSearchList": ".list a",
        "ruleSearchName": "h3@text",
        "ruleBookName": ".book h1@text",
        "ruleBookContent": "#content@html",
        "ruleChapterList": "#list a",
        "httpUserAgent": "Mozilla/5.0",
        "lastUpdateTime": "100",
    }

    normalized = _normalize_source_config(source)

    assert normalized["searchUrl"] == "search?key={{key}}"
    assert normalized["exploreUrl"] == "category/{{page}}"
    assert normalized["ruleSearch"]["bookList"] == ".list a"
    assert normalized["ruleBookInfo"]["name"] == ".book h1@text"
    assert normalized["ruleToc"]["chapterList"] == "##list a"
    assert normalized["ruleContent"]["content"] == "##content@html"
    assert '"User-Agent":"Mozilla/5.0"' in normalized["header"]
    assert normalized["lastUpdateTime"] == "100"
    assert "ruleSearchUrl" not in normalized


def test_very_old_flat_source_is_converted_to_new_format():
    from app.api.routes.yuedu import _normalize_source_config

    source = {
        "bookSourceName": "Very Old",
        "bookSourceUrl": "https://old.example",
        "enable": True,
        "searchUrl": "search?keyword={{key}}",
        "searchList": "div.result-list > div",
        "searchName": "h3 a@text",
        "searchAuthor": "p.author@text",
        "searchCover": "img@src",
        "searchIntro": "p.intro@text",
        "searchKind": "p.kind@text",
        "searchDetailUrl": "h3 a@href",
        "bookDetailUrl": "div.book-info a@href",
        "bookInfoRule": {
            "name": "h1@text",
            "author": "p.author@text",
            "cover": "img@src",
            "intro": "div.intro@text",
            "catalogUrl": "div.chapter-list a:first-child@href",
        },
        "catalogRule": {
            "chapterList": "a",
            "chapterName": "@text",
            "chapterUrl": "@href",
        },
        "contentRule": {
            "content": "p@text",
            "nextContent": "a.next@href",
            "filter": ["\u5e7f\u544a"],
        },
        "contentReplace": ["\u66f4\u591a"],
        "headers": {"User-Agent": "Mozilla/5.0"},
        "lastUpdateTime": "100",
    }

    normalized = _normalize_source_config(source)

    assert normalized["enabled"] is True
    assert normalized["ruleSearch"]["bookList"] == "div.result-list > div"
    assert normalized["ruleSearch"]["bookUrl"] == "h3 a@href"
    assert normalized["ruleSearch"]["kind"] == "p.kind@text"
    assert normalized["ruleBookInfo"]["name"] == "h1@text"
    assert normalized["ruleBookInfo"]["tocUrl"] == "div.chapter-list a:first-child@href"
    assert normalized["ruleToc"]["chapterList"] == "a"
    assert normalized["ruleContent"]["content"] == "p@text"
    assert normalized["ruleContent"]["nextContentUrl"] == "a.next@href"
    assert normalized["ruleContent"]["replaceRegex"] == ["\u5e7f\u544a"]
    assert normalized["header"] == '{"User-Agent":"Mozilla/5.0"}'
    assert "catalogRule" not in normalized
