"""Celery client helpers for enqueueing crawler tasks from the API process."""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "novelhub",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
)

celery_app.conf.update(
    task_default_queue="crawl",
    task_routes={
        "tasks.crawl_all_source": {"queue": "crawl"},
    },
)


def enqueue_crawl_all(source_id: str, max_pages: int = 200, task_id: str | None = None) -> str:
    result = celery_app.send_task(
        "tasks.crawl_all_source",
        args=[source_id, max_pages, task_id],
        queue="crawl",
    )
    return result.id
