from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Cookie, CrawlTask, Source, User
from app.repositories.crawl_task import CrawlTaskRepository
from app.repositories.crawl_log import CrawlLogRepository
from app.schemas.crawl import CrawlLogOut, CrawlTaskOut
from app.services.auth import get_current_user
from app.services.cookie_crypto import safe_decrypt_cookie
from app.services.task_queue import enqueue_crawl_all

router = APIRouter(prefix="/crawl", tags=["crawl"], dependencies=[Depends(get_current_user)])


def _is_admin(user: User) -> bool:
    return user.role in ("admin", "super_admin")


def _can_access_source(user: User, source: Source) -> bool:
    return _is_admin(user) or (
        source.owner_id is not None and source.owner_id == user.id
    )


def _can_access_task(user: User, task: CrawlTask) -> bool:
    return _is_admin(user) or task.user_id is None or task.user_id == user.id


class CrawlTaskCreateRequest(BaseModel):
    source: str
    max_pages: int = 0
    priority: int = 0
    exclude_tags: list[str] = Field(default_factory=list)
    exclude_categories: list[str] = Field(default_factory=list)


def _clean_filter_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(
        value.strip()
        for value in values
        if value and value.strip()
    ))


@router.post("/tasks", response_model=CrawlTaskOut, status_code=202)
async def create_crawl_task(
    payload: CrawlTaskCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create and enqueue a background full-site crawl task."""
    source = await db.get(Source, payload.source)
    if source is None or not source.enabled:
        raise HTTPException(status_code=404, detail="Source not found or disabled")
    if not _can_access_source(user, source):
        raise HTTPException(status_code=404, detail="Source not found or disabled")

    task = CrawlTask(
        id=str(uuid4()),
        source=payload.source,
        mode="discover_all",
        max_pages=payload.max_pages,
        exclude_tags=_clean_filter_values(payload.exclude_tags),
        exclude_categories=_clean_filter_values(payload.exclude_categories),
        priority=payload.priority,
        status="pending",
        user_id=user.id,
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CrawlTaskRepository(db)
    return await repo.list_recent(
        offset=offset,
        limit=limit,
        user_id=None if _is_admin(user) else user.id,
    )


@router.get("/tasks/{task_id}", response_model=CrawlTaskOut)
async def get_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CrawlTaskRepository(db)
    task = await repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if not _can_access_task(user, task):
        raise HTTPException(status_code=404, detail="Crawl task not found")
    return task


@router.get("/tasks/{task_id}/logs", response_model=list[CrawlLogOut])
async def list_task_logs(
    task_id: str,
    offset: int = 0,
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CrawlLogRepository(db)
    task = await db.get(CrawlTask, task_id)
    if task is None or not _can_access_task(user, task):
        raise HTTPException(status_code=404, detail="Crawl task not found")
    return await repo.list_by_task(task_id, offset=offset, limit=limit)


@router.post("/tasks/{task_id}/pause")
async def pause_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CrawlTaskRepository(db)
    task = await repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if not _can_access_task(user, task):
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if task.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail=f"Cannot pause task in status: {task.status}")
    if task.status != "paused":
        task.status = "paused"
    task.resume_at = None
    await db.commit()
    return {"task_id": task.id, "status": task.status}


@router.post("/tasks/{task_id}/resume")
async def resume_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CrawlTaskRepository(db)
    task = await repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if not _can_access_task(user, task):
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if task.status != "paused":
        raise HTTPException(status_code=400, detail=f"Cannot resume task in status: {task.status}")
    task.status = "pending"
    task.resume_at = None
    await db.commit()
    return {"task_id": task.id, "status": task.status}


@router.post("/tasks/{task_id}/move-front")
async def move_task_front(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Move a queued task to the front of the DB-backed crawl queue."""
    repo = CrawlTaskRepository(db)
    task = await repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if not _can_access_task(user, task):
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if task.status not in ("pending", "paused"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reorder task in status: {task.status}",
        )
    top_priority = await db.scalar(
        select(func.max(CrawlTask.priority)).where(
            CrawlTask.status.in_(["pending", "paused"])
        )
    )
    task.priority = (top_priority or 0) + 1
    task.resume_at = None
    await db.commit()
    return {"task_id": task.id, "status": task.status, "priority": task.priority}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CrawlTaskRepository(db)
    task = await repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if not _can_access_task(user, task):
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if task.status in ("completed", "failed", "cancelled", "completed_with_errors"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel task in status: {task.status}")
    task.status = "cancelled"
    task.resume_at = None
    task.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    return {"task_id": task.id, "status": task.status}


@router.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed crawl task with auto-resume (skips already-downloaded chapters)."""
    repo = CrawlTaskRepository(db)
    task = await repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if not _can_access_task(user, task):
        raise HTTPException(status_code=404, detail="Crawl task not found")
    if task.status not in ("failed", "completed_with_errors"):
        raise HTTPException(status_code=400, detail=f"Cannot retry task in status: {task.status}")

    if getattr(task, "mode", "bookshelf") == "discover_all":
        task.status = "pending"
        task.error = None
        task.result = None
        task.progress = None
        task.resume_at = None
        task.started_at = None
        task.finished_at = None
        await db.commit()
        enqueue_crawl_all(task.source, task.max_pages or 200, task.id)
        return {"task_id": task.id, "status": "pending"}

    from app.crawler.registry import get_plugin
    from app.services.sync import SyncService

    task.status = "running"
    task.error = None
    task.resume_at = None
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
