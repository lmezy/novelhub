"""Fan-out ("search every source") behaviour.

The single-source endpoint answers with one page of one site; these tests pin the
properties that make searching many sites at once usable: every visible source is
queried concurrently, each one's results and failures stay attributed to it, a
slow or dead source cannot hold the others back, and the visibility gate is the
same one the single-source endpoint applies.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.services.auth import get_current_user
from app.services.search_fanout import (
    can_search_source,
    fanout_concurrency,
    fanout_max_sources,
    fanout_results_per_source,
    fanout_search,
    fanout_source_timeout,
    search_async,
    serialize_item,
    visible_sources,
)


def _source(source_id: str, name: str, **overrides):
    values = {
        "id": source_id,
        "name": name,
        "enabled": True,
        "plugin_name": "yuedu",
        "config": {},
        "is_r18": False,
        "owner_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(role: str = "super_admin", **overrides):
    values = {"id": "user-1", "role": role, "r18_enabled": True, "non_r18_enabled": True}
    values.update(overrides)
    return SimpleNamespace(**values)


def _db(sources, library_rows=None):
    db = MagicMock()
    db.scalars = AsyncMock(return_value=list(sources))
    db.execute = AsyncMock(return_value=list(library_rows or []))
    return db


def _filtering_db(sources):
    """A session whose ``scalars`` honours the ``enabled`` / ``id IN (...)`` filters.

    ``visible_sources`` builds the restriction into SQL, so a mock that returns
    every row would let a broken "narrow to these ids" pass unnoticed.  This
    reads the compiled statement's own parameters back instead of guessing.
    """
    db = MagicMock()
    db.execute = AsyncMock(return_value=[])

    async def scalars(statement):
        # ``id IN (__[POSTCOMPILE_...])`` keeps its values under a numbered key
        # (``id_1``/``id_2``/…) that shifts with the rest of the statement, so
        # find it by prefix rather than by an exact name.
        params = statement.compile().params
        wanted: set[str] = set()
        for key, value in params.items():
            if key.startswith("id_") and isinstance(value, (list, tuple, set)):
                wanted.update(str(entry) for entry in value)
        rows = [
            source
            for source in sources
            if getattr(source, "enabled", True)
            and (not wanted or source.id in wanted)
        ]
        return sorted(rows, key=lambda source: source.name)

    db.scalars = AsyncMock(side_effect=scalars)
    return db


def _plugin(results=None, *, error=None, delay=0.0):
    async def search_books(keyword, page=1, limit=50):
        if delay:
            await asyncio.sleep(delay)
        if error is not None:
            raise error
        return list(results or [])[:limit]

    return SimpleNamespace(search_books=search_books)


def _item(name="Book", url="https://example.com/novel/1.html"):
    return {"name": name, "author": "A", "bookUrl": url, "lastChapter": "Ch1"}


# --------------------------------------------------------------------------
# configuration knobs
# --------------------------------------------------------------------------


def test_fanout_settings_have_safe_defaults():
    assert fanout_concurrency() >= 1
    assert fanout_source_timeout() > 0
    assert fanout_results_per_source() >= 1
    assert fanout_max_sources() >= 0


def test_fanout_concurrency_of_zero_is_not_taken_literally():
    """A concurrency of 0 must not deadlock the fan-out."""
    with patch("app.services.search_fanout.settings") as settings:
        settings.SEARCH_FANOUT_CONCURRENCY = 0
        assert fanout_concurrency() == 8

        settings.SEARCH_FANOUT_CONCURRENCY = "many"
        assert fanout_concurrency() == 8

        settings.SEARCH_FANOUT_CONCURRENCY = 3
        assert fanout_concurrency() == 3


# --------------------------------------------------------------------------
# visibility
# --------------------------------------------------------------------------


def test_admin_sees_every_source():
    assert can_search_source(_user("super_admin"), _source("a", "A", owner_id="other"))
    assert can_search_source(_user("admin"), _source("a", "A", is_r18=True))


def test_non_admin_cannot_search_someone_elses_source():
    user = _user("user")
    assert can_search_source(user, _source("a", "A", owner_id="user-1")) is True
    assert can_search_source(user, _source("a", "A", owner_id="user-2")) is False
    assert can_search_source(user, _source("a", "A", owner_id=None)) is True


def test_r18_preference_gates_sources_for_non_admins():
    """The single-source endpoint's R18 gate has to hold for the fan-out too."""
    neither = _user("user", r18_enabled=False, non_r18_enabled=False)
    assert can_search_source(neither, _source("a", "A", is_r18=False)) is False
    assert can_search_source(neither, _source("a", "A", is_r18=True)) is False

    only_all_ages = _user("user", r18_enabled=False, non_r18_enabled=True)
    assert can_search_source(only_all_ages, _source("a", "A", is_r18=False)) is True
    assert can_search_source(only_all_ages, _source("a", "A", is_r18=True)) is False

    only_r18 = _user("user", r18_enabled=True, non_r18_enabled=False)
    assert can_search_source(only_r18, _source("a", "A", is_r18=False)) is False
    assert can_search_source(only_r18, _source("a", "A", is_r18=True)) is True


@pytest.mark.asyncio
async def test_visible_sources_drops_what_the_user_may_not_search():
    sources = [
        _source("mine", "Mine", owner_id="user-1"),
        _source("theirs", "Theirs", owner_id="user-2"),
        _source("global", "Global", owner_id=None),
    ]
    db = _db(sources)

    visible = await visible_sources(db, _user("user"))
    assert [source.id for source in visible] == ["mine", "global"]


@pytest.mark.asyncio
async def test_visible_sources_narrows_to_named_ids_without_bypassing_the_gate():
    sources = [
        _source("mine", "Mine", owner_id="user-1"),
        _source("theirs", "Theirs", owner_id="user-2"),
    ]
    db = _db(sources)

    visible = await visible_sources(db, _user("user"), ["theirs", "mine"])
    assert [source.id for source in visible] == ["mine"]


# --------------------------------------------------------------------------
# the fan-out itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_source_is_searched_and_reported_separately():
    sources = [_source("s1", "One"), _source("s2", "Two")]
    db = _db(sources)
    produced = [
        _plugin([_item("First")]),
        _plugin([_item("Second"), _item("Third", "https://example.com/novel/2.html")]),
    ]

    def ordered(name, config=None):
        # ``searchable_plugin`` builds one plugin per source in list order.
        return produced.pop(0)

    with patch("app.services.search_fanout.get_plugin", side_effect=ordered):
        result = await fanout_search(db, _user(), "keyword")

    assert result["sources"] == 2
    assert result["failures"] == []
    by_id = {entry["source_id"]: entry for entry in result["results"]}
    assert by_id["s1"]["count"] == 1
    assert by_id["s2"]["count"] == 2
    assert [row["name"] for row in by_id["s2"]["results"]] == ["Second", "Third"]
    assert by_id["s1"]["source_name"] == "One"


@pytest.mark.asyncio
async def test_one_failing_source_does_not_hide_the_others():
    sources = [_source("good", "Good"), _source("bad", "Bad")]
    db = _db(sources)
    produced = [_plugin([_item("Found")]), _plugin(error=RuntimeError("403 验证码"))]

    def ordered(name, config=None):
        return produced.pop(0)

    with patch("app.services.search_fanout.get_plugin", side_effect=ordered):
        result = await fanout_search(db, _user(), "keyword")

    assert [entry["source_id"] for entry in result["results"]] == ["good"]
    assert result["results"][0]["results"][0]["name"] == "Found"
    assert len(result["failures"]) == 1
    assert result["failures"][0]["source_id"] == "bad"
    assert "403" in result["failures"][0]["error"]


@pytest.mark.asyncio
async def test_a_hanging_source_times_out_and_the_others_still_answer():
    """One dead host must never decide how long the whole search takes."""
    sources = [_source("fast", "Fast"), _source("dead", "Dead")]
    db = _db(sources)
    produced = [
        _plugin([_item("Fast result")]),
        _plugin([_item("Never")], delay=30),
    ]

    def ordered(name, config=None):
        return produced.pop(0)

    with patch("app.services.search_fanout.get_plugin", side_effect=ordered), patch(
        "app.services.search_fanout.settings"
    ) as settings:
        settings.SEARCH_FANOUT_SOURCE_TIMEOUT_SECONDS = 0.05
        settings.SEARCH_FANOUT_CONCURRENCY = 4
        settings.SEARCH_FANOUT_MAX_SOURCES = 0
        settings.SEARCH_FANOUT_RESULTS_PER_SOURCE = 20
        result = await fanout_search(db, _user(), "keyword")

    assert [entry["source_id"] for entry in result["results"]] == ["fast"]
    assert len(result["failures"]) == 1
    assert result["failures"][0]["source_id"] == "dead"
    assert "timeout" in result["failures"][0]["error"]


@pytest.mark.asyncio
async def test_sources_are_queried_concurrently_not_one_after_another():
    """N sources each taking ``delay`` must finish in about one delay."""
    delay = 0.05
    sources = [_source(f"s{i}", f"S{i}") for i in range(4)]
    db = _db(sources)

    def ordered(name, config=None):
        return _plugin([_item()], delay=delay)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with patch("app.services.search_fanout.get_plugin", side_effect=ordered), patch(
        "app.services.search_fanout.settings"
    ) as settings:
        settings.SEARCH_FANOUT_SOURCE_TIMEOUT_SECONDS = 5.0
        settings.SEARCH_FANOUT_CONCURRENCY = 4
        settings.SEARCH_FANOUT_MAX_SOURCES = 0
        settings.SEARCH_FANOUT_RESULTS_PER_SOURCE = 20
        result = await fanout_search(db, _user(), "keyword")
    elapsed = loop.time() - started

    assert len(result["results"]) == 4
    # Sequentially this would be 4 * delay; allow a generous margin for CI.
    assert elapsed < delay * 3


@pytest.mark.asyncio
async def test_results_stream_out_before_the_slowest_source_finishes():
    """The streamed view exists so fast sites paint first."""
    sources = [_source("fast", "Fast"), _source("slow", "Slow")]
    db = _db(sources)
    produced = [_plugin([_item("Fast")]), _plugin([_item("Slow")], delay=0.2)]

    def ordered(name, config=None):
        return produced.pop(0)

    seen = []
    with patch("app.services.search_fanout.get_plugin", side_effect=ordered), patch(
        "app.services.search_fanout.settings"
    ) as settings:
        settings.SEARCH_FANOUT_SOURCE_TIMEOUT_SECONDS = 5.0
        settings.SEARCH_FANOUT_CONCURRENCY = 2
        settings.SEARCH_FANOUT_MAX_SOURCES = 0
        settings.SEARCH_FANOUT_RESULTS_PER_SOURCE = 20
        async for event in search_async(db, _user(), "keyword"):
            seen.append(event)

    assert seen[0] == {"type": "start", "sources": 2}
    assert seen[-1] == {"type": "done", "searched": 2, "failed": 0}
    assert [event["source_id"] for event in seen[1:3]] == ["fast", "slow"]


@pytest.mark.asyncio
async def test_a_source_without_search_books_is_skipped_not_failed():
    sources = [_source("local", "Local", plugin_name="local_markdown"), _source("y", "Y")]
    db = _db(sources)
    db.scalars = AsyncMock(return_value=sources)

    def ordered(name, config=None):
        if name == "local_markdown":
            return SimpleNamespace()  # no search_books at all
        return _plugin([_item("Hit")])

    with patch("app.services.search_fanout.get_plugin", side_effect=ordered):
        result = await fanout_search(db, _user(), "keyword")

    assert result["sources"] == 1
    assert result["failures"] == []
    assert result["results"][0]["source_id"] == "y"


@pytest.mark.asyncio
async def test_plugin_construction_failure_is_not_a_crash():
    sources = [_source("broken", "Broken"), _source("ok", "OK")]
    db = _db(sources)

    def ordered(name, config=None):
        # Both sources use the same plugin name here, so discriminate by call.
        if ordered.calls == 0:
            ordered.calls += 1
            raise ValueError("bad source JSON")
        ordered.calls += 1
        return _plugin([_item("Fine")])
    ordered.calls = 0

    with patch("app.services.search_fanout.get_plugin", side_effect=ordered):
        result = await fanout_search(db, _user(), "keyword")

    assert result["sources"] == 1
    assert result["results"][0]["source_id"] == "ok"


@pytest.mark.asyncio
async def test_empty_keyword_short_circuits_without_touching_a_source():
    db = _db([_source("s1", "One")])
    with patch("app.services.search_fanout.get_plugin") as get_plugin:
        result = await fanout_search(db, _user(), "   ")
    assert result == {"query": "   ", "sources": 0, "results": [], "failures": []}
    get_plugin.assert_not_called()


@pytest.mark.asyncio
async def test_no_searchable_source_reports_an_empty_run():
    db = _db([])
    with patch("app.services.search_fanout.get_plugin") as get_plugin:
        events = [event async for event in search_async(db, _user(), "keyword")]
    assert events == [
        {"type": "start", "sources": 0},
        {"type": "done", "searched": 0, "failed": 0},
    ]
    get_plugin.assert_not_called()


@pytest.mark.asyncio
async def test_per_source_limit_is_forwarded_to_the_plugin():
    sources = [_source("s1", "One")]
    db = _db(sources)
    calls = []

    async def search_books(keyword, page=1, limit=50):
        calls.append((keyword, page, limit))
        return [_item(f"Book {index}") for index in range(limit)]

    with patch(
        "app.services.search_fanout.get_plugin",
        return_value=SimpleNamespace(search_books=search_books),
    ):
        result = await fanout_search(db, _user(), "keyword", limit_per_source=3)

    assert calls == [("keyword", 1, 3)]
    assert result["results"][0]["count"] == 3


# --------------------------------------------------------------------------
# library hydration
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_results_already_in_the_library_carry_their_book_id():
    sources = [_source("s1", "One")]
    db = _db(sources, library_rows=[("book-9", "https://example.com/novel/1.html")])

    with patch(
        "app.services.search_fanout.get_plugin",
        return_value=_plugin([_item("Known")]),
    ):
        result = await fanout_search(db, _user(), "keyword")

    row = result["results"][0]["results"][0]
    assert row["in_library"] is True
    assert row["book_id"] == "book-9"


@pytest.mark.asyncio
async def test_library_lookup_failure_does_not_lose_the_search_results():
    sources = [_source("s1", "One")]
    db = _db(sources)
    db.execute = AsyncMock(side_effect=RuntimeError("connection reset"))

    with patch(
        "app.services.search_fanout.get_plugin",
        return_value=_plugin([_item("Found")]),
    ):
        result = await fanout_search(db, _user(), "keyword")

    assert result["failures"] == []
    row = result["results"][0]["results"][0]
    assert row["name"] == "Found"
    assert row["in_library"] is False
    assert row["book_id"] is None


def test_serialize_item_strips_legado_url_options_before_matching():
    """Book links carry a ``,{...}`` option suffix; the library stores the clean form."""
    item = {
        "name": "Suffixed",
        "bookUrl": 'https://example.com/novel/7.html,{"method":"POST","body":"x=1"}',
    }
    row = serialize_item(item, {"https://example.com/novel/7.html": "book-7"})
    assert row["in_library"] is True
    assert row["book_id"] == "book-7"


def test_serialize_item_matches_on_the_last_path_segment():
    item = {"name": "Segment", "bookUrl": "https://example.com/novel/42.html"}
    row = serialize_item(item, {"42.html": "book-42"})
    assert row["in_library"] is True


def test_serialize_item_defaults_missing_fields():
    row = serialize_item({}, {})
    assert row["name"] == "Unknown"
    assert row["author"] == "Unknown"
    assert row["in_library"] is False
    assert row["book_id"] is None


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_all_sources_endpoint_returns_each_source():
    sources = [_source("s1", "One"), _source("s2", "Two")]
    db = _db(sources)
    produced = [_plugin([_item("A")]), _plugin([_item("B")])]

    def ordered(name, config=None):
        return produced.pop(0)

    async def fake_user():
        return _user()

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db] = lambda: db
    try:
        with patch("app.services.search_fanout.get_plugin", side_effect=ordered):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/sources/search", params={"q": "hello"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "hello"
    assert body["sources"] == 2
    assert {entry["source_id"] for entry in body["results"]} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_search_all_sources_endpoint_requires_a_keyword():
    async def fake_user():
        return _user()

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db] = lambda: _db([])
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/sources/search", params={"q": "  "})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_streaming_endpoint_sends_sse_frames_per_source():
    sources = [_source("s1", "One")]
    db = _db(sources)

    async def fake_user():
        return _user()

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db] = lambda: db
    try:
        with patch(
            "app.services.search_fanout.get_plugin",
            return_value=_plugin([_item("Streamed")]),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/sources/search/stream", params={"q": "hi"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[0] == {"type": "start", "sources": 1}
    assert events[1]["type"] == "source"
    assert events[1]["results"][0]["name"] == "Streamed"
    assert events[2]["type"] == "done"
    assert events[-1] == {"type": "end"}


@pytest.mark.asyncio
async def test_streaming_endpoint_narrows_to_requested_sources():
    sources = [_source("s1", "One"), _source("s2", "Two")]
    db = _filtering_db(sources)

    async def fake_user():
        return _user()

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db] = lambda: db
    try:
        with patch(
            "app.services.search_fanout.get_plugin",
            return_value=_plugin([_item("Only")]),
        ) as get_plugin:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/sources/search/stream",
                    params={"q": "hi", "sources": "s2"},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    events = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    reported = [event["source_id"] for event in events if event.get("type") == "source"]
    assert reported == ["s2"]
    # Only the requested source's plugin was ever built.
    assert get_plugin.call_count == 1


@pytest.mark.asyncio
async def test_streaming_endpoint_searches_every_source_when_none_are_named():
    sources = [_source("s1", "One"), _source("s2", "Two"), _source("s3", "Three")]
    db = _filtering_db(sources)

    async def fake_user():
        return _user()

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db] = lambda: db
    try:
        with patch(
            "app.services.search_fanout.get_plugin",
            return_value=_plugin([_item("All")]),
        ) as get_plugin:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/sources/search/stream", params={"q": "hi"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    events = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[0] == {"type": "start", "sources": 3}
    assert sorted(
        event["source_id"] for event in events if event.get("type") == "source"
    ) == ["s1", "s2", "s3"]
    assert get_plugin.call_count == 3


@pytest.mark.asyncio
async def test_disabled_sources_are_never_searched():
    sources = [_source("on", "On"), _source("off", "Off", enabled=False)]
    db = _filtering_db(sources)

    with patch(
        "app.services.search_fanout.get_plugin",
        return_value=_plugin([_item("Hit")]),
    ) as get_plugin:
        result = await fanout_search(db, _user(), "keyword")

    assert [entry["source_id"] for entry in result["results"]] == ["on"]
    assert get_plugin.call_count == 1


@pytest.mark.asyncio
async def test_fanout_does_not_shadow_the_single_source_endpoint():
    """``/sources/search`` must not be read as a source id called "search"."""
    from app.api.routes.sources import router

    paths = [route.path for route in router.routes]
    assert "/sources/search" in paths
    assert "/sources/search/stream" in paths
    assert "/sources/{source_id}/search" in paths
