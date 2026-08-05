from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.services.auth import get_current_user
from app.services.bookshelf import (
    book_group_ids,
    create_user_group,
    delete_user_group,
    list_user_groups,
    set_book_groups,
    set_books_groups,
    update_user_group,
)


router = APIRouter(prefix="/bookshelf", tags=["bookshelf"])


class GroupCreateRequest(BaseModel):
    name: str
    show: bool = True


class GroupUpdateRequest(BaseModel):
    name: str | None = None
    show: bool | None = None


class SetGroupsRequest(BaseModel):
    group_ids: list[str] = []


class BatchSetGroupsRequest(BaseModel):
    book_ids: list[str] = []
    group_ids: list[str] = []


@router.get("/groups")
async def get_groups(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_user_groups(db, user)


@router.post("/groups", status_code=201)
async def create_group(
    payload: GroupCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await create_user_group(db, user, payload.name, payload.show)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/groups/{group_id}")
async def update_group(
    group_id: str,
    payload: GroupUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await update_user_group(
            db,
            user,
            group_id,
            payload.name,
            payload.show,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_user_group(db, user, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/favorites/{book_id}/groups")
async def get_book_groups(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {"group_ids": await book_group_ids(db, user, book_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/favorites/{book_id}/groups")
async def put_book_groups(
    book_id: str,
    payload: SetGroupsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {
            "group_ids": await set_book_groups(
                db,
                user,
                book_id,
                payload.group_ids,
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/favorites/batch-groups")
async def put_books_groups(
    payload: BatchSetGroupsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {
            "updated": await set_books_groups(
                db,
                user,
                payload.book_ids,
                payload.group_ids,
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
