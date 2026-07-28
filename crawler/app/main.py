"""Celery worker — processes crawl tasks from the shared Redis queue."""
import subprocess
import sys


def main() -> None:
    subprocess.run(
        [
            sys.executable, "-m", "celery",
            "-A", "celery_app",
            "worker",
            "-l", "info",
        ],
        cwd="/app/scheduler_app",
        check=False,
    )


if __name__ == "__main__":
    main()
