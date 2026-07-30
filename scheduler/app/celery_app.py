"""Celery application for NovelHub scheduled crawl tasks.

Shared by both the scheduler (beat) and crawler (worker) containers.
"""

import os

from celery import Celery
from celery.schedules import crontab

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")

app = Celery(
    "novelhub",
    broker=f"redis://{REDIS_HOST}:{REDIS_PORT}/0",
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_retry_delay=300,  # 5 minutes
    task_max_retries=3,
    task_default_queue="crawl",
    task_queues={
        "crawl": {"exchange": "crawl", "routing_key": "crawl"},
    },
    task_routes={
        "tasks.daily_sync_all": {"queue": "crawl"},
        "tasks.sync_single_source": {"queue": "crawl"},
        "tasks.resync_all_books": {"queue": "crawl"},
    },
    beat_schedule={
        "daily-sync-all-sources": {
            "task": "tasks.daily_sync_all",
            "schedule": crontab(hour=3, minute=0),
        },
        "cookie-health-check": {
            "task": "tasks.check_cookie_health",
            "schedule": crontab(hour=2, minute=0),
        },
    },
)

# Register tasks by importing the module after app is created
import tasks  # noqa: E402, F401
