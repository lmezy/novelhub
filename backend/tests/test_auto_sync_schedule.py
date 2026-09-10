"""Scheduler-side tests for the periodic (auto) sync task.

The beat task lives in ``scheduler/app/tasks.py``; importing it needs the
scheduler directory on ``sys.path`` (the container sets it through
PYTHONPATH).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


SCHEDULER_DIR = Path(__file__).resolve().parents[2] / "scheduler" / "app"
if str(SCHEDULER_DIR) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_DIR))

import tasks as scheduler_tasks  # noqa: E402


class _FakeSessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *args):
        return False


class _Rows(list):
    """Mimic ``ScalarResult`` (the scheduler calls ``.all()`` on it)."""

    def all(self):
        return list(self)


def _db_with_sources(sources, active_sources):
    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[_Rows(sources), _Rows(active_sources)])
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_auto_sync_check_creates_bounded_tasks_when_due():
    sources = [SimpleNamespace(id="src-a"), SimpleNamespace(id="src-b")]
    db = _db_with_sources(sources, [])

    with (
        patch("app.core.database.SessionLocal", lambda: _FakeSessionContext(db)),
        patch(
            "app.services.settings.get_auto_sync_settings",
            AsyncMock(return_value={
                "enabled": True,
                "time": "03:00",
                "interval_hours": 6,
            }),
        ),
        patch(
            "app.services.settings.get_auto_sync_last_run",
            AsyncMock(return_value=""),
        ),
        patch(
            "app.services.settings.set_auto_sync_last_run",
            AsyncMock(),
        ),
    ):
        result = await scheduler_tasks._auto_sync_check_async()

    assert result["tasks_created"] == 2
    created = [call.args[0] for call in db.add.call_args_list]
    assert [task.source for task in created] == ["src-a", "src-b"]
    # Bounded crawl: an unbounded (max_pages<=0) automatic task would never
    # finish on a WAF-protected source.
    assert all(task.max_pages == 3 for task in created)
    assert all(task.mode == "discover_all" for task in created)


@pytest.mark.asyncio
async def test_auto_sync_check_skips_sources_with_running_task():
    sources = [SimpleNamespace(id="src-a"), SimpleNamespace(id="src-b")]
    db = _db_with_sources(sources, ["src-a"])

    with (
        patch("app.core.database.SessionLocal", lambda: _FakeSessionContext(db)),
        patch(
            "app.services.settings.get_auto_sync_settings",
            AsyncMock(return_value={
                "enabled": True,
                "time": "03:00",
                "interval_hours": 6,
            }),
        ),
        patch(
            "app.services.settings.get_auto_sync_last_run",
            AsyncMock(return_value=""),
        ),
        patch(
            "app.services.settings.set_auto_sync_last_run",
            AsyncMock(),
        ),
    ):
        result = await scheduler_tasks._auto_sync_check_async()

    assert result["tasks_created"] == 1
    created = [call.args[0] for call in db.add.call_args_list]
    assert [task.source for task in created] == ["src-b"]


@pytest.mark.asyncio
async def test_auto_sync_check_respects_interval_and_creates_nothing():
    sources = [SimpleNamespace(id="src-a")]
    db = _db_with_sources(sources, [])
    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)

    with (
        patch("app.core.database.SessionLocal", lambda: _FakeSessionContext(db)),
        patch(
            "app.services.settings.get_auto_sync_settings",
            AsyncMock(return_value={
                "enabled": True,
                "time": "03:00",
                "interval_hours": 6,
            }),
        ),
        patch(
            "app.services.settings.get_auto_sync_last_run",
            AsyncMock(return_value=now.isoformat(timespec="seconds")),
        ),
        patch(
            "app.services.settings.set_auto_sync_last_run",
            AsyncMock(),
        ),
    ):
        result = await scheduler_tasks._auto_sync_check_async()

    assert result == {"enabled": True, "due": False}
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_auto_sync_check_returns_early_when_disabled():
    db = AsyncMock()

    with (
        patch("app.core.database.SessionLocal", lambda: _FakeSessionContext(db)),
        patch(
            "app.services.settings.get_auto_sync_settings",
            AsyncMock(return_value={
                "enabled": False,
                "time": "03:00",
                "interval_hours": 0,
            }),
        ),
    ):
        result = await scheduler_tasks._auto_sync_check_async()

    assert result == {"enabled": False}
