"""Applying a reviewed source patch, and the ``update`` proposal flow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.source_patch import apply_source_patch, deep_merge


def make_source(**overrides):
    values = {
        "id": "yuedu_abc",
        "name": "示例书源",
        "url": "https://example.com",
        "plugin_name": "yuedu",
        "enabled": True,
        "is_r18": False,
        "config": {
            "bookSourceName": "示例书源",
            "ruleToc": {"chapterList": "class.chapter-list@li@a"},
            "concurrentRate": "1000",
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---------------------------------------------------------------------------
# deep_merge / apply_source_patch
# ---------------------------------------------------------------------------


def test_deep_merge_only_touches_the_patched_keys():
    base = {"a": {"b": 1, "c": 2}, "d": [1, 2]}
    patch = {"a": {"b": 9}, "d": [3]}

    assert deep_merge(base, patch) == {"a": {"b": 9, "c": 2}, "d": [3]}


def test_patch_merges_config_and_leaves_other_rules_alone():
    source = make_source()

    applied = apply_source_patch(source, {
        "config": {"ruleToc": {"chapterList": "#list li a"}},
    })

    assert applied == ["config.ruleToc.chapterList"]
    assert source.config["ruleToc"]["chapterList"] == "#list li a"
    # Untouched rules survive: a proposal must not wipe the book source.
    assert source.config["bookSourceName"] == "示例书源"
    assert source.config["concurrentRate"] == "1000"


def test_patch_can_toggle_a_source_column():
    source = make_source(enabled=True)

    applied = apply_source_patch(source, {"enabled": False, "is_r18": True})

    assert applied == ["enabled", "is_r18"]
    assert source.enabled is False
    assert source.is_r18 is True


def test_patch_coerces_a_string_boolean_and_ignores_junk_columns():
    source = make_source(enabled=True)

    applied = apply_source_patch(source, {
        "enabled": "false",
        "plugin_name": {"nested": "not allowed"},
        "owner_id": "someone-else",
        "unexpected": "x",
    })

    assert applied == ["enabled"]
    assert source.enabled is False
    # Only allow-listed columns are touched; nothing else appears on the model.
    assert source.plugin_name == "yuedu"
    assert not hasattr(source, "owner_id")


def test_patch_reports_nothing_when_values_are_unchanged():
    source = make_source()

    assert apply_source_patch(source, {"config": {"bookSourceName": "示例书源"}}) == []
    assert apply_source_patch(source, {"enabled": True}) == []


def test_patch_can_raise_the_request_interval():
    """The AI's fix for a rate-limited site: 1 request per N seconds."""
    source = make_source()

    applied = apply_source_patch(source, {"sync_interval_seconds": "60"})

    assert applied == ["sync_interval_seconds"]
    assert source.sync_interval_seconds == 60


def test_patch_clamps_and_rejects_bogus_intervals():
    source = make_source()

    apply_source_patch(source, {"sync_interval_seconds": 999_999})
    assert source.sync_interval_seconds == 3600

    apply_source_patch(source, {"sync_interval_seconds": -30})
    assert source.sync_interval_seconds == 0

    # A boolean is not an interval, and junk must leave the stored value alone.
    source.sync_interval_seconds = 60
    assert apply_source_patch(source, {"sync_interval_seconds": True}) == []
    assert apply_source_patch(source, {"sync_interval_seconds": "soon"}) == []
    assert source.sync_interval_seconds == 60

    # An empty value clears it back to the source's own rate.
    assert apply_source_patch(source, {"sync_interval_seconds": None}) == [
        "sync_interval_seconds"
    ]
    assert source.sync_interval_seconds is None


def test_patch_refuses_non_serialisable_and_oversized_payloads():
    source = make_source()

    assert apply_source_patch(source, {"config": {"x": object()}}) == []
    assert apply_source_patch(source, {"config": {"x": "y" * 500_000}}) == []
    assert apply_source_patch(source, None) == []


# ---------------------------------------------------------------------------
# update proposals through the API
# ---------------------------------------------------------------------------


def fake_admin():
    return SimpleNamespace(id="admin-1", role="super_admin", r18_enabled=True,
                           non_r18_enabled=True)


def fake_db(*, source=None, change=None, rows=None):
    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, key: (
        source if model.__name__ == "Source" else change
    ))
    db.scalars = AsyncMock(return_value=list(rows or []))
    db.scalar = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


async def call(method, path, db, *, json=None, user=None, admin=True):
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_db
    from app.main import app
    from app.services.auth import get_current_user, require_admin

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: (user or fake_admin())
    if admin:
        # The review endpoint stores ``reviewer.id``, so the override must
        # return a user rather than ``None``.
        app.dependency_overrides[require_admin] = lambda: (user or fake_admin())
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=json)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_update_change_applies_immediately():
    source = make_source()
    db = fake_db(source=source)

    resp = await call("POST", "/api/source-changes", db, json={
        "action": "update",
        "source_id": "yuedu_abc",
        "source_data": {"patch": {"config": {"ruleToc": {"chapterList": "li a"}}}},
    })

    assert resp.status_code == 201
    assert source.config["ruleToc"]["chapterList"] == "li a"
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_update_requires_a_source_id():
    resp = await call("POST", "/api/source-changes", fake_db(),
                      json={"action": "update"})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_of_a_missing_source_is_404():
    db = fake_db(source=None)

    resp = await call("POST", "/api/source-changes", db, json={
        "action": "update", "source_id": "gone", "source_data": {"patch": {}},
    })

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reviewing_an_update_proposal_writes_it_to_the_source():
    from app.models import SourceChange

    source = make_source()
    change = SourceChange(
        id="ch1", user_id="u1", action="update", source_id="yuedu_abc",
        source_data={
            "patch": {"config": {"ruleToc": {"chapterList": "li a"}}},
            "diff": [],
            "origin": "ai",
        },
        status="pending",
    )
    db = fake_db(source=source, change=change)

    resp = await call("POST", "/api/source-changes/ch1/review", db,
                      json={"action": "approve", "note": "看起来对"})

    assert resp.status_code == 200
    assert source.config["ruleToc"]["chapterList"] == "li a"
    assert change.status == "approved"
    assert change.review_note == "看起来对"


@pytest.mark.asyncio
async def test_rejecting_an_update_proposal_changes_nothing():
    from app.models import SourceChange

    source = make_source()
    change = SourceChange(
        id="ch1", user_id="u1", action="update", source_id="yuedu_abc",
        source_data={"patch": {"config": {"ruleToc": {"chapterList": "li a"}}}},
        status="pending",
    )
    db = fake_db(source=source, change=change)

    resp = await call("POST", "/api/source-changes/ch1/review", db,
                      json={"action": "reject"})

    assert resp.status_code == 200
    assert source.config["ruleToc"]["chapterList"] == "class.chapter-list@li@a"
    assert change.status == "rejected"
