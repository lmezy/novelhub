from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.services.auth import get_current_user
from app.services.custom_tags import (
    apply_custom_tag,
    delete_custom_tag,
    list_book_custom_tags,
    remove_custom_tag_application,
    update_custom_tag,
)


router = APIRouter(prefix="/custom-tags", tags=["custom-tags"])


class ApplyTagRequest(BaseModel):
    book_id: str
    name: str
    is_public: bool = False
    show_user: bool = True


class UpdateTagRequest(BaseModel):
    is_public: bool | None = None
    show_user: bool | None = None


@router.get("/books/{book_id}")
async def get_book_tags(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_book_custom_tags(db, book_id, user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/apply")
async def apply_tag(
    payload: ApplyTagRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await apply_custom_tag(
            db,
            payload.book_id,
            user,
            payload.name,
            payload.is_public,
            payload.show_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/books/{book_id}/tags/{tag_id}")
async def remove_tag(
    book_id: str,
    tag_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await remove_custom_tag_application(db, book_id, tag_id, user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{tag_id}")
async def update_tag(
    tag_id: str,
    payload: UpdateTagRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await update_custom_tag(
            db,
            tag_id,
            user,
            payload.is_public,
            payload.show_user,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{tag_id}", status_code=204)
async def remove_tag_definition(
    tag_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_custom_tag(db, tag_id, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
