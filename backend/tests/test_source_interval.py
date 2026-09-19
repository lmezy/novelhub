"""Per-source request interval (``sources.sync_interval_seconds``).

The interval is the fix for sites that publish a 拉取间隔 ("one request per
minute"): the book source JSON ships a much faster ``concurrentRate`` and the
sync gets captcha-blocked, so the admin UI can override the pace per source.
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from app.crawler.plugins.yuedu import YueduPlugin
from app.services.source_interval import (
    MAX_SYNC_INTERVAL_SECONDS,
    apply_source_interval,
    clamp_sync_interval,
    source_sync_interval,
)


@pytest.fixture(autouse=True)
def _clear_rate_state():
    """The limiter's state is class-level; never leak it between tests."""
    YueduPlugin._rate_locks.clear()
    YueduPlugin._rate_state.clear()
    yield
    YueduPlugin._rate_locks.clear()
    YueduPlugin._rate_state.clear()


class _SleepRecorder:
    """Stand-in for ``asyncio`` inside the plugin module, recording sleeps."""

    Lock = asyncio.Lock

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __getattr__(self, name):  # delegate everything else to the real module
        return getattr(asyncio, name)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _plugin(config: dict | None = None) -> YueduPlugin:
    return YueduPlugin(config or {"bookSourceUrl": "https://example.test"})


def _patch_rate_limiter(monkeypatch, fake) -> None:
    """Point the limiter's ``asyncio`` at ``fake`` so nothing really sleeps.

    ``_sleep_rate_limit`` used to live in the package ``__init__`` and every test
    here patched that module's global.  It now lives in a mixin module of its
    own, so patching the package global silently missed and the tests fell
    through to real 60-second sleeps.  Resolving the *owning* module from the
    function keeps this correct wherever the limiter moves next.
    """
    owner = sys.modules[YueduPlugin._sleep_rate_limit.__module__]
    monkeypatch.setattr(owner, "asyncio", fake)


# ---------------------------------------------------------------------------
# value handling
# ---------------------------------------------------------------------------


def test_clamp_sync_interval_normalises_every_input():
    assert clamp_sync_interval(None) is None
    assert clamp_sync_interval("") is None
    assert clamp_sync_interval("abc") is None
    assert clamp_sync_interval(True) is None
    assert clamp_sync_interval(0) == 0
    assert clamp_sync_interval("60") == 60
    assert clamp_sync_interval(-5) == 0
    assert clamp_sync_interval(99_999) == MAX_SYNC_INTERVAL_SECONDS


def test_source_sync_interval_reads_the_column():
    assert source_sync_interval(SimpleNamespace(sync_interval_seconds=60)) == 60
    assert source_sync_interval(SimpleNamespace(sync_interval_seconds=None)) is None
    # A source-like object without the column at all (older rows/plugins).
    assert source_sync_interval(SimpleNamespace(id="x")) is None


def test_apply_source_interval_is_a_no_op_without_the_hook():
    """Nothing must break for plugins that cannot be throttled."""
    plugin = SimpleNamespace()
    assert apply_source_interval(plugin, SimpleNamespace(sync_interval_seconds=60)) == 60


def test_apply_source_interval_hands_the_value_to_the_plugin():
    seen: list[int | None] = []
    plugin = SimpleNamespace(set_request_interval_seconds=seen.append)

    apply_source_interval(plugin, SimpleNamespace(sync_interval_seconds=60))
    apply_source_interval(plugin, SimpleNamespace(sync_interval_seconds=None))

    assert seen == [60, None]


def test_set_request_interval_seconds_ignores_garbage():
    plugin = _plugin()

    plugin.set_request_interval_seconds(60)
    assert plugin._request_interval_seconds == 60
    plugin.set_request_interval_seconds("45")
    assert plugin._request_interval_seconds == 45
    plugin.set_request_interval_seconds(-3)
    assert plugin._request_interval_seconds == 0
    plugin.set_request_interval_seconds("nonsense")
    assert plugin._request_interval_seconds is None
    plugin.set_request_interval_seconds(None)
    assert plugin._request_interval_seconds is None


# ---------------------------------------------------------------------------
# the limiter itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configured_interval_paces_requests(monkeypatch):
    fake = _SleepRecorder()
    _patch_rate_limiter(monkeypatch, fake)

    plugin = _plugin()
    plugin.set_request_interval_seconds(60)

    # The first request of a source is never delayed: the slot starts at 0.
    await plugin._sleep_rate_limit()
    assert fake.sleeps == []

    await plugin._sleep_rate_limit()
    # 60s minus the (tiny) elapsed time, plus the built-in jitter.
    assert len(fake.sleeps) == 1
    assert 59.0 <= fake.sleeps[0] <= 60.7


@pytest.mark.asyncio
async def test_configured_interval_beats_concurrent_rate(monkeypatch):
    fake = _SleepRecorder()
    _patch_rate_limiter(monkeypatch, fake)

    # 搬山人 ships concurrentRate=1000 (1s) while the site wants a minute.
    plugin = _plugin({"bookSourceUrl": "https://bs.test", "concurrentRate": "1000"})
    plugin.set_request_interval_seconds(60)

    await plugin._sleep_rate_limit()
    await plugin._sleep_rate_limit()

    assert 59.0 <= fake.sleeps[0] <= 60.7


@pytest.mark.asyncio
async def test_zero_interval_means_explicitly_unthrottled(monkeypatch):
    fake = _SleepRecorder()
    _patch_rate_limiter(monkeypatch, fake)

    plugin = _plugin({"bookSourceUrl": "https://off.test", "concurrentRate": "1000"})
    plugin.set_request_interval_seconds(0)

    for _ in range(3):
        await plugin._sleep_rate_limit()

    assert fake.sleeps == []


@pytest.mark.asyncio
async def test_unset_interval_keeps_the_source_concurrent_rate(monkeypatch):
    fake = _SleepRecorder()
    _patch_rate_limiter(monkeypatch, fake)

    plugin = _plugin({"bookSourceUrl": "https://rate.test", "concurrentRate": "2/1000"})

    await plugin._sleep_rate_limit()
    await plugin._sleep_rate_limit()
    await plugin._sleep_rate_limit()

    # count/window: the third request has to wait out the 1s window.
    assert fake.sleeps
    assert 0.5 <= fake.sleeps[0] <= 1.1


@pytest.mark.asyncio
async def test_ignore_rate_limit_still_wins_over_a_configured_interval(monkeypatch):
    from app.core.config import settings

    fake = _SleepRecorder()
    _patch_rate_limiter(monkeypatch, fake)
    monkeypatch.setattr(settings, "SYNC_IGNORE_RATE_LIMIT", True)

    plugin = _plugin()
    plugin.set_request_interval_seconds(60)

    await plugin._sleep_rate_limit()
    await plugin._sleep_rate_limit()

    assert fake.sleeps == []
