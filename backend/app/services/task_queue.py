"""Crawl queue helpers.

Full-site crawl tasks are now DB-backed: the API writes a ``crawl_tasks`` row
with ``status=pending`` and the crawler worker polls that table in priority
order. No Celery message is needed for manual crawl tasks.
"""


def enqueue_crawl_all(source_id: str, max_pages: int = 200, task_id: str | None = None) -> str:
    """Return the task id; the DB queue worker picks it up automatically."""
    return task_id or ""
