"""Crawler container entry point.

Runs the DB-backed crawl queue worker plus the Celery worker that executes
scheduled sync tasks (daily sync, cookie health, resync).
"""

import subprocess
import sys
import time


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

    try:
        while True:
            if queue_proc.poll() is not None:
                raise SystemExit(f"queue_worker exited with code {queue_proc.returncode}")
            if celery_proc.poll() is not None:
                raise SystemExit(f"celery worker exited with code {celery_proc.returncode}")
            time.sleep(5)
    finally:
        queue_proc.terminate()
        celery_proc.terminate()


if __name__ == "__main__":
    main()
