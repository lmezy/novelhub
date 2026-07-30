"""Celery beat scheduler -- dispatches periodic sync tasks.

Run inside the scheduler container to trigger daily-sync-all-sources
and any other beat-scheduled tasks.
"""

import subprocess
import sys


def main() -> None:
    subprocess.run(
        [
            sys.executable, "-m", "celery",
            "-A", "celery_app",
            "beat",
            "-l", "info",
        ],
        cwd="/app/scheduler_app",
        check=False,
    )


if __name__ == "__main__":
    main()
