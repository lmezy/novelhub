from loguru import logger

from app.core.database import SessionLocal
from app.models import Source
from app.repositories.source import SourceRepository
from app.repositories.crawl_task import CrawlTaskRepository
from app.repositories.crawl_log import CrawlLogRepository
from app.models.crawl_task import CrawlTask
from app.models.crawl_log import CrawlLog
from app.services.sync import SyncService
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import select


async def run_crawl_for_source(source_id: str) -> None:
    async with SessionLocal() as db:
        source_repo = SourceRepository(db)
        task_repo = CrawlTaskRepository(db)
        log_repo = CrawlLogRepository(db)

        source = await source_repo.get(source_id)
        if source is None or not source.enabled:
            logger.warning("Source {} not found or disabled, skipping crawl", source_id)
            return

        task = CrawlTask(
            id=str(uuid4()),
            source=source_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        await task_repo.add(task)
        await db.commit()

        try:
            sync_service = SyncService(db)
            result = await sync_service.sync_book(source.id, source.url)
            task.status = "completed"
            task.finished_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info("Crawl task {} completed: {}", task.id, result)
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.finished_at = datetime.now(timezone.utc)
            await db.commit()
            logger.opt(exception=exc).error("Crawl task {} failed", task.id)
