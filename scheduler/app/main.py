"""Celery beat scheduler -- dispatches periodic sync tasks.

The crawler container runs the worker that consumes the scheduler queue.
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
