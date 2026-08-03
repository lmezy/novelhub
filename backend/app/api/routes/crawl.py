from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Cookie, CrawlTask, Source, User
from app.repositories.crawl_task import CrawlTaskRepository
from app.repositories.crawl_log import CrawlLogRepository
from app.schemas.crawl import CrawlLogOut, CrawlTaskOut
from app.services.auth import require_admin
from app.services.cookie_crypto import safe_decrypt_cookie
from app.services.task_queue import enqueue_crawl_all

router = APIRouter(prefix="/crawl", tags=["crawl"], dependencies=[Depends(require_admin)])


class CrawlTaskCreateRequest(BaseModel):
    source: str
    max_pages: int = 200


@router.post("/tasks", response_model=CrawlTaskOut, status_code=202)
async def create_crawl_task(
    payload: CrawlTaskCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create and enqueue a background full-site crawl task."""
    source = await db.get(Source, payload.source)
    if source is None or not source.enabled:
        raise HTTPException(status_code=404, detail="Source not found or disabled")

    task = CrawlTask(
        id=str(uuid4()),
        source=payload.source,
        mode="discover_all",
        max_pages=payload.max_pages,
        status="pending",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    enqueue_crawl_all(payload.source, payload.max_pages, task.id)
    return task


@router.get("/tasks", response_model=list[CrawlTaskOut])
async def list_tasks(
    offset: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    repo = CrawlTaskRepository(db)
    return await repo.list_recent(offset=offset, limit=limit)


@router.get("/tasks/{task_id}", response_model=CrawlTaskOut)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    repo = CrawlTaskRepository(db)
    task = await repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawl task not found")
    return task


@router.get("/tasks/{task_id}/logs", response_model=list[CrawlLogOut])
async def list_task_logs(
    task_id: str,
    offset: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    repo = CrawlLogRepository(db)
    return await repo.list_by_task(task_id, offset=offset, limit=limit)


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """Retry a failed crawl task with auto-resume (skips already-downloaded chapters)."""
    repo = CrawlTaskRepository(db)
    task = await repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if task.status not in ("failed", "completed_with_errors"):
        raise HTTPException(status_code=400, detail=f"Cannot retry task in status: {task.status}")

    if getattr(task, "mode", "bookshelf") == "discover_all":
        task.status = "pending"
        task.error = None
        task.result = None
        task.started_at = None
        task.finished_at = None
        await db.commit()
        enqueue_crawl_all(task.source, task.max_pages or 200, task.id)
        return {"task_id": task.id, "status": "pending"}

    from app.crawler.registry import get_plugin
    from app.services.sync import SyncService

    task.status = "running"
    task.error = None
    task.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    task.finished_at = None
    await db.commit()

    try:
        svc = SyncService(db)
        if task.source == "*":
            result = await svc.sync_bookshelf(task.source)
        else:
            cookie = await db.scalar(select(Cookie).where(Cookie.source == task.source))
            if cookie:
                plugin = get_plugin(task.source)
                plugin.set_cookie(safe_decrypt_cookie(cookie.cookie_data))
            result = await svc.sync_bookshelf(task.source)
        task.status = "completed"
        task.result = result
        task.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        return {"task_id": task_id, "status": "completed", "result": result}
    except ValueError as exc:
        task.status = "failed"
        task.error = str(exc)
        task.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)
        task.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Retry failed: {str(exc)[:300]}") from exc
