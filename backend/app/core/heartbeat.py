"""Liveness signal the crawl queue worker leaves for its process supervisor.

The queue worker runs as a child of ``crawler/app/main.py`` -- PID 1 of the
crawler container -- and that supervisor can only see whether the child has
*exited*.  A worker whose loop hangs stays alive forever, so nothing restarts
it.  Online on 2026-09-22 a database restart made the queue loop die: the
coroutine was gone, ``asyncio.run`` then hung in its own teardown, and the tasks
created afterwards sat ``pending`` while the sync page showed "排队" with nothing
running -- until the container was restarted by hand an hour later.

The signal is a file: the queue loop touches it on every iteration, and the
supervisor restarts the container once it goes stale.

Nothing here may import application settings.  PID 1 loads this module to decide
whether to restart, so a configuration mistake in it would turn into a crash
loop of the container instead of a warning.
"""

import os
import time

#: Where the signal lives.  Both processes read the same variable, so a
#: deployment can move the file without touching code.
HEARTBEAT_PATH = (
    os.getenv("SYNC_QUEUE_HEARTBEAT_PATH") or "/tmp/novelhub_queue_heartbeat"
)

_DEFAULT_TIMEOUT_SECONDS = 300.0


def _timeout_seconds() -> float:
    """``SYNC_QUEUE_HEARTBEAT_TIMEOUT_SECONDS``, refusing to be switched off.

    Junk and non-positive values mean "use the default", the way every other
    ``SYNC_*`` timeout in :mod:`app.core.config` behaves: a mistyped variable
    must not silently disable the watchdog.
    """
    try:
        value = float(os.getenv("SYNC_QUEUE_HEARTBEAT_TIMEOUT_SECONDS", ""))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS


#: How long the signal may stay untouched before the worker counts as hung.
#: The queue loop iterates at least every two seconds even when it starts no
#: task, so only a hang -- never a slow source -- can reach this.
HEARTBEAT_TIMEOUT_SECONDS = _timeout_seconds()


def mark_alive(path: str | None = None) -> None:
    """Record that the queue loop is running.  Never raises.

    A signal that cannot be written must not break the queue itself;
    :func:`arm_heartbeat` lets the supervisor notice and fall back to watching
    for an exited child only.
    """
    try:
        with open(path or HEARTBEAT_PATH, "w") as handle:
            handle.write(f"{time.time():.0f}\n")
    except OSError:
        pass


def seconds_since_mark(path: str | None = None) -> float | None:
    """Age of the signal in seconds, or ``None`` when it was never written."""
    try:
        return max(0.0, time.time() - os.path.getmtime(path or HEARTBEAT_PATH))
    except OSError:
        return None


def heartbeat_is_stale(path: str | None = None) -> bool:
    """Whether the signal is missing, or older than the timeout."""
    age = seconds_since_mark(path)
    return age is None or age > HEARTBEAT_TIMEOUT_SECONDS


def arm_heartbeat(path: str | None = None) -> bool:
    """Start a fresh signal and report whether the watchdog can be trusted.

    Writing one here also covers the worker's own startup (imports plus
    ``_reset_stale_running_tasks``), and overwriting the file means a timestamp
    left behind by an earlier run can never pass for a live worker.
    """
    target = path or HEARTBEAT_PATH
    mark_alive(target)
    return seconds_since_mark(target) is not None
