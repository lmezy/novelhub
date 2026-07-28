from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Source, SourceChange, User
from app.schemas.source_change import SourceChangeCreate, SourceChangeOut, SourceChangeReview
from app.services.auth import get_current_user, require_admin

router = APIRouter(prefix="/source-changes", tags=["source-changes"])


@router.get("", response_model=list[SourceChangeOut])
async def list_changes(
    status: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(SourceChange).order_by(SourceChange.created_at.desc())
    if status:
        q = q.where(SourceChange.status == status)
    if user.role == "user":
        q = q.where(SourceChange.user_id == user.id)
    result = await db.scalars(q)
    return list(result)


@router.post("", response_model=SourceChangeOut, status_code=201)
async def create_change(
    payload: SourceChangeCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.action not in ("create", "delete"):
        raise HTTPException(status_code=400, detail="Action must be 'create' or 'delete'")

    if user.role in ("admin", "super_admin"):
        if payload.action == "create" and payload.source_data:
            sd = payload.source_data
            if await db.get(Source, sd.get("id")):
                raise HTTPException(status_code=409, detail="Source already exists")
            source = Source(**sd)
            db.add(source)
            await db.commit()
        elif payload.action == "delete" and payload.source_id:
            source = await db.get(Source, payload.source_id)
            if source:
                await db.delete(source)
                await db.commit()
        change = SourceChange(
            id=str(uuid4()),
            user_id=user.id,
            action=payload.action,
            source_id=payload.source_id,
            source_data=payload.source_data,
            status="approved",
            reviewer_id=user.id,
            reviewed_at=datetime.now(timezone.utc),
        )
        return change

    change = SourceChange(
        id=str(uuid4()),
        user_id=user.id,
        action=payload.action,
        source_id=payload.source_id,
        source_data=payload.source_data,
        status="pending",
    )
    db.add(change)
    await db.commit()
    await db.refresh(change)
    return change


@router.post("/{change_id}/review", response_model=SourceChangeOut)
async def review_change(
    change_id: str,
    payload: SourceChangeReview,
    reviewer: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    change = await db.get(SourceChange, change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="Change not found")
    if change.status != "pending":
        raise HTTPException(status_code=400, detail="Change already reviewed")

    if payload.action == "approve":
        if change.action == "create" and change.source_data:
            sd = change.source_data
            if not await db.get(Source, sd.get("id")):
                source = Source(**sd)
                db.add(source)
        elif change.action == "delete" and change.source_id:
            source = await db.get(Source, change.source_id)
            if source:
                await db.delete(source)

        change.status = "approved"
    elif payload.action == "reject":
        change.status = "rejected"
    else:
        raise HTTPException(status_code=400, detail="Review action must be 'approve' or 'reject'")

    change.reviewer_id = reviewer.id
    change.review_note = payload.note
    change.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(change)
    return change
