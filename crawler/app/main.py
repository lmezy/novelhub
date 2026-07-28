"""Celery worker -- processes crawl tasks from the dedicated crawl queue."""
import subprocess
import sys


def main() -> None:
    subprocess.run(
        [
            sys.executable, "-m", "celery",
            "-A", "celery_app",
            "worker",
            "-Q", "crawl",
            "-l", "info",
        ],
        cwd="/app/scheduler_app",
        check=False,
    )


if __name__ == "__main__":
    main()