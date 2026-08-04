from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Tag, User
from app.repositories.tag import TagRepository
from app.schemas.tag import TagCreate, TagOut
from app.services.auth import get_current_user, require_admin
from app.services.visibility import R18_TAGS

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
async def list_tags(
    offset: int = 0,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = TagRepository(db)
    tags = await repo.list(offset=offset, limit=limit)
    if user.role not in ("admin", "super_admin"):
        tags = [tag for tag in tags if tag.name not in R18_TAGS]
    return tags


@router.post("", response_model=TagOut, status_code=201, dependencies=[Depends(require_admin)])
async def create_tag(payload: TagCreate, db: AsyncSession = Depends(get_db)):
    repo = TagRepository(db)
    existing = await repo.get_by_name(payload.name)
    if existing:
        raise HTTPException(status_code=409, detail="Tag already exists")
    tag = Tag(id=str(uuid4()), name=payload.name)
    return await repo.add(tag)


@router.delete("/{tag_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_tag(tag_id: str, db: AsyncSession = Depends(get_db)):
    repo = TagRepository(db)
    tag = await repo.get(tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    await repo.delete(tag)
