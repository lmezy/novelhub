"""Liveness of the crawl queue loop, and the signal its supervisor watches.

Online on 2026-09-22 a database restart made the queue loop die: the coroutine
never ran again, ``asyncio.run`` hung in its own teardown, and -- because PID 1
of the crawler container only watched for an *exited* child -- the container
stayed up while every task created afterwards sat ``pending`` for an hour.

These tests pin both halves of the repair: the loop retries instead of ending,
and the signal it leaves behind goes stale when it stops ticking.
"""

import asyncio
import os
import time
from unittest.mock import patch

import pytest

from app.core import heartbeat
from app.core.config import settings
from app.services.crawl_runner import _worker_loop


# ---------------------------------------------------------------------------
# The signal itself
# ---------------------------------------------------------------------------


def test_the_signal_does_not_exist_until_the_loop_marks_itself_alive(tmp_path):
    path = str(tmp_path / "beat")

    assert heartbeat.seconds_since_mark(path) is None
    # No signal at all is not "healthy": a worker that never wrote one must not
    # be able to look alive.
    assert heartbeat.heartbeat_is_stale(path) is True

    heartbeat.mark_alive(path)

    assert heartbeat.seconds_since_mark(path) == pytest.approx(0, abs=2)
    assert heartbeat.heartbeat_is_stale(path) is False


def test_a_signal_older_than_the_timeout_counts_as_hung(tmp_path, monkeypatch):
    path = str(tmp_path / "beat")
    heartbeat.mark_alive(path)
    monkeypatch.setattr(heartbeat, "HEARTBEAT_TIMEOUT_SECONDS", 60.0)
    # Age the file instead of waiting a minute for it.
    old = time.time() - 120
    os.utime(path, (old, old))

    assert heartbeat.seconds_since_mark(path) >= 100
    assert heartbeat.heartbeat_is_stale(path) is True


def test_an_unwritable_signal_disables_the_watchdog_instead_of_raising(tmp_path):
    """A filesystem that refuses the write must not restart the container forever."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x")
    path = str(blocked / "beat")

    heartbeat.mark_alive(path)  # never raises
    assert heartbeat.seconds_since_mark(path) is None
    assert heartbeat.heartbeat_is_stale(path) is True
    assert heartbeat.arm_heartbeat(path) is False


def test_arming_starts_a_fresh_signal_over_whatever_a_previous_run_left(
    tmp_path, monkeypatch
):
    """A stale file from an earlier run must not pass for a live worker."""
    path = str(tmp_path / "beat")
    heartbeat.mark_alive(path)
    old = time.time() - 3600
    os.utime(path, (old, old))
    monkeypatch.setattr(heartbeat, "HEARTBEAT_TIMEOUT_SECONDS", 60.0)
    assert heartbeat.heartbeat_is_stale(path) is True

    assert heartbeat.arm_heartbeat(path) is True
    assert heartbeat.heartbeat_is_stale(path) is False


# ---------------------------------------------------------------------------
# The loop that has to survive a database blip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_loop_retries_after_a_database_error_instead_of_dying():
    """The online outage: one failed poll must not end the queue for good.

    The database restarting under a running worker raised out of the loop's own
    poll.  Nothing polls an empty pending queue afterwards, so the only repair
    is for the loop to log, back off and poll again.
    """
    polls: list[str | None] = []
    recovered = asyncio.Event()

    async def failing_then_healthy(limit, exclude_sources=None):
        polls.append(exclude_sources)
        if len(polls) == 1:
            raise OSError(
                "cannot call Transaction.rollback(): the underlying connection "
                "is closed"
            )
        recovered.set()
        return []

    with (
        patch.object(settings, "SYNC_WORKER_CONCURRENCY", 0),
        patch("app.services.crawl_runner._LOOP_ERROR_BACKOFF_SECONDS", 0.01),
        patch("app.services.crawl_runner.mark_alive"),
        patch(
            "app.services.crawl_runner._next_pending_tasks",
            side_effect=failing_then_healthy,
        ),
    ):
        worker = asyncio.create_task(_worker_loop())
        try:
            await asyncio.wait_for(recovered.wait(), timeout=8)
            # The error was logged and the loop is still polling: this is the
            # assertion that would have failed before the repair.
            assert worker.done() is False
        finally:
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker

    assert len(polls) >= 2
    assert worker.cancelled()


@pytest.mark.asyncio
async def test_worker_loop_marks_its_liveness_on_every_iteration():
    """Without the mark the container supervisor cannot see a wedged worker."""
    polled = asyncio.Event()

    async def fake_next(limit, exclude_sources=None):
        polled.set()
        return []

    with (
        patch.object(settings, "SYNC_WORKER_CONCURRENCY", 0),
        patch("app.services.crawl_runner.mark_alive") as marked,
        patch("app.services.crawl_runner._next_pending_tasks", side_effect=fake_next),
    ):
        worker = asyncio.create_task(_worker_loop())
        try:
            await asyncio.wait_for(polled.wait(), timeout=8)
        finally:
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker

    assert marked.call_count >= 1
