from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "novelhub",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_routes={
        "tasks.crawl_source": {"queue": "crawl"},
    },
    beat_schedule={
        "daily-sync-all-sources": {
            "task": "tasks.sync_all_sources",
            "schedule": 86400.0,
        },
        "daily-incremental-backup": {
            "task": "tasks.daily_backup",
            "schedule": 86400.0,
        },
        "weekly-full-backup": {
            "task": "tasks.weekly_backup",
            "schedule": 604800.0,
        },
        "check-cookie-expiry": {
            "task": "tasks.check_cookie_expiry",
            "schedule": 43200.0,
        },
    },
)

import tasks  # noqa: F401 -- register tasks for discovery