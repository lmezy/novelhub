from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.crawl_task import CrawlTaskRepository
from app.repositories.crawl_log import CrawlLogRepository
from app.schemas.crawl import CrawlLogOut, CrawlTaskOut

router = APIRouter(prefix="/crawl", tags=["crawl"], dependencies=[Depends(require_admin)])


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
from app.models import User
from app.services.auth import require_admin
