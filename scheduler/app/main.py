"""Celery beat + worker -- scheduled sync dispatch and backup tasks."""
import subprocess
import sys


def main() -> None:
    subprocess.run(
        [
            sys.executable, "-m", "celery",
            "-A", "celery_app",
            "worker",
            "-B",
            "-Q", "celery",
            "-l", "info",
        ],
        cwd="/app/scheduler_app",
        check=False,
    )


if __name__ == "__main__":
    main()