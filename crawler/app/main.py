"""Crawler container entry point.

Runs the DB-backed crawl queue worker plus the Celery worker that executes
scheduled sync tasks (daily sync, cookie health, resync).
"""

import os
import subprocess
import sys
import threading
import time

from app.core.heartbeat import (
    HEARTBEAT_TIMEOUT_SECONDS,
    arm_heartbeat,
    heartbeat_is_stale,
)


def _reap_orphans(tracked: set[int]) -> None:
    """Reap zombie grandchildren (Chromium / crashpad) that landed on PID 1.

    Playwright launches Chromium through its Node driver.  When that driver
    exits, the browser processes are reparented to PID 1 -- this supervisor --
    and stay zombies because nothing ever waits for them.  A long sync leaves
    thousands of ``chrome``/``chrome_crashpad`` zombies behind (1287 were
    observed after a single day on 《要撸小说》), so peek at exited children and
    reap everything except the processes this supervisor manages itself.
    """
    while True:
        try:
            info = os.waitid(
                os.P_ALL,
                0,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except (AttributeError, ChildProcessError, OSError):
            return
        if info is None:
            return
        pid = info.si_pid
        if pid in tracked:
            # Popen.poll() still needs this exit status; try again next round.
            return
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            return


def _start_orphan_reaper(tracked: set[int]) -> None:
    def _loop() -> None:
        while True:
            _reap_orphans(tracked)
            time.sleep(15)

    threading.Thread(target=_loop, name="orphan-reaper", daemon=True).start()


def _start_queue_watchdog() -> bool:
    """Arm the queue worker's liveness signal and report if it can be trusted.

    ``Popen.poll`` only ever sees an *exit*, so it cannot tell a busy queue
    worker from a wedged one.  Online on 2026-09-22 the queue loop died and the
    process then hung in asyncio's own teardown: this supervisor kept the
    container up for an hour while the queue consumed nothing, and every task
    created in that time sat ``pending``.  The heartbeat file (see
    ``app.core.heartbeat``) is what closes that gap.

    A filesystem that refuses the write disables the watchdog instead of
    restarting the container forever, and writing a fresh signal here also
    overwrites whatever an earlier run left behind.
    """
    if arm_heartbeat():
        return True
    print(
        "queue heartbeat could not be written; watching for an exited "
        "queue_worker only",
        file=sys.stderr,
    )
    return False


def main() -> None:
    queue_proc = subprocess.Popen(
        [sys.executable, "-m", "queue_worker"],
        cwd="/app/crawler_app",
    )
    celery_proc = subprocess.Popen(
        [
            sys.executable, "-m", "celery",
            "-A", "celery_app",
            "worker",
            "-Q", "scheduler",
            "--concurrency", "1",
            "-l", "info",
        ],
        cwd="/app/scheduler_app",
    )
    _start_orphan_reaper({queue_proc.pid, celery_proc.pid})
    # Armed after the child exists: the fresh signal has to cover the worker's
    # own startup (imports plus ``_reset_stale_running_tasks``) before it ticks.
    watchdog = _start_queue_watchdog()

    try:
        while True:
            if queue_proc.poll() is not None:
                raise SystemExit(f"queue_worker exited with code {queue_proc.returncode}")
            if celery_proc.poll() is not None:
                raise SystemExit(f"celery worker exited with code {celery_proc.returncode}")
            if watchdog and heartbeat_is_stale():
                # Alive but not working.  Exiting is the repair: ``restart:
                # always`` brings back a worker that consumes tasks again, and
                # its startup reset re-queues the rows this one's death left
                # sitting as ``running``.
                raise SystemExit(
                    "queue_worker is not running its queue loop (no heartbeat "
                    f"for {int(HEARTBEAT_TIMEOUT_SECONDS)}s)"
                )
            time.sleep(5)
    finally:
        queue_proc.terminate()
        celery_proc.terminate()


if __name__ == "__main__":
    main()
