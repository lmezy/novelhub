"""Celery beat + worker entrypoint — scheduled syncs and crawl task execution."""
import subprocess
import sys


def main() -> None:
    subprocess.run(
        [
            sys.executable, "-m", "celery",
            "-A", "celery_app",
            "worker",
            "-B",
            "-l", "info",
        ],
        cwd="/app/scheduler_app",
        check=False,
    )


if __name__ == "__main__":
    main()
