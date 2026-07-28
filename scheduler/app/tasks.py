import asyncio
from celery.utils.log import get_task_logger

from celery_app import celery_app

logger = get_task_logger(__name__)


@celery_app.task(name="tasks.sync_all_sources")
def sync_all_sources() -> dict:
    from app.core.database import SessionLocal
    from app.repositories.source import SourceRepository

    async def _run():
        async with SessionLocal() as db:
            repo = SourceRepository(db)
            sources = await repo.list_enabled()
            logger.info("Daily sync: found %d enabled sources", len(sources))

            results = []
            for source in sources:
                logger.info("Triggering crawl for source=%s", source.id)
                crawl_source.delay(source.id)
                results.append({"source_id": source.id, "status": "queued"})

            return {"sources_queued": len(results), "results": results}

    return asyncio.run(_run())


@celery_app.task(name="tasks.crawl_source", bind=True, max_retries=3)
def crawl_source(self, source_id: str) -> dict:
    import asyncio

    async def _run():
        from crawler_service import run_crawl_for_source
        await run_crawl_for_source(source_id)
        return {"source_id": source_id, "status": "completed"}

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception("Crawl failed for source=%s, retry=%d", source_id, self.request.retries)
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="tasks.daily_backup")
def daily_backup() -> dict:
    """Daily incremental backup -- runs every day."""
    import asyncio

    async def _run():
        from app.services.backup import BackupService
        path = await BackupService().create_incremental_backup()
        logger.info("Daily backup complete: %s", path)
        return {"path": path, "status": "completed"}

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception("Daily backup failed")
        return {"status": "failed", "error": str(exc)}


@celery_app.task(name="tasks.weekly_backup")
def weekly_backup() -> dict:
    """Weekly full backup -- runs every Sunday."""
    import asyncio

    async def _run():
        from app.services.backup import BackupService
        path = await BackupService().create_full_backup()
        logger.info("Weekly full backup complete: %s", path)
        return {"path": path, "status": "completed"}

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception("Weekly backup failed")
        return {"status": "failed", "error": str(exc)}
