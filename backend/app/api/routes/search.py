from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Literal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, User
from app.services.auth import get_current_user
from app.services.visibility import can_view_all_ages, can_view_r18
from pydantic import BaseModel, Field

from app.services.search import search_service


class SearchResult(BaseModel):
    hits: list[dict]
    total: int
    offset: int
    limit: int


class SearchCondition(BaseModel):
    field: Literal[
        "title",
        "author",
        "chapter_title",
        "description",
        "content",
        "tags",
        "category",
    ]
    mode: Literal["exact", "fuzzy"] = "exact"
    value: str = ""


class AdvancedSearchRequest(BaseModel):
    conditions: list[SearchCondition] = Field(default_factory=list)
    match: Literal["and", "or"] = "and"
    scope: Literal["all", "books", "chapters"] = "all"
    tag: str | None = None
    source_id: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


router = APIRouter(prefix="/search", tags=["search"])


async def _filter_visible_hits(
    user: User,
    db: AsyncSession,
    hits: list[dict],
) -> list[dict]:
    if user.role in ("admin", "super_admin"):
        return hits
    book_ids = {
        str(hit.get("book_id") or hit.get("id") or "")
        for hit in hits
    }
    book_ids.discard("")
    if not book_ids:
        return []
    rows = await db.execute(
        select(Book.id, Book.owner_id, Book.is_public).where(Book.id.in_(book_ids))
    )
    visibility_map = {
        str(book_id): (owner_id, is_public)
        for book_id, owner_id, is_public in rows.all()
    }
    return [
        hit
        for hit in hits
        if _is_visible_book(
            visibility_map.get(str(hit.get("book_id") or hit.get("id") or "")),
            user.id,
        )
    ]


def _is_visible_book(meta: tuple[str | None, bool] | None, user_id: str) -> bool:
    if meta is None:
        return False
    owner_id, is_public = meta
    return owner_id is None or owner_id == user_id or is_public


@router.get("", response_model=SearchResult)
async def search(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    q: str = Query(default="", description="Search query"),
    scope: str = Query("books", pattern="^(books|chapters)$"),
    tag: str | None = Query(default=None, description="Book tag name"),
    offset: int = 0,
    limit: int = 20,
):
    allow_r18 = can_view_r18(user)
    allow_all_ages = can_view_all_ages(user)
    if scope == "books":
        result = search_service.search_books(
            q,
            offset=offset,
            limit=limit,
            allow_r18=allow_r18,
            allow_all_ages=allow_all_ages,
            tag=tag,
        )
    else:
        result = search_service.search_chapters(
            q,
            offset=offset,
            limit=limit,
            allow_r18=allow_r18,
            allow_all_ages=allow_all_ages,
            tag=tag,
        )

    hits = await _filter_visible_hits(user, db, result["hits"])
    return SearchResult(
        hits=hits,
        total=(
            int(result.get("total", result.get("estimatedTotalHits", len(hits))))
            if user.role in ("admin", "super_admin")
            else len(hits)
        ),
        offset=result.get("offset", offset),
        limit=result.get("limit", limit),
    )


@router.post("/advanced", response_model=SearchResult)
async def advanced_search(
    payload: AdvancedSearchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    allow_r18 = can_view_r18(user)
    allow_all_ages = can_view_all_ages(user)
    result = search_service.advanced_search(
        [condition.model_dump() for condition in payload.conditions],
        match=payload.match,
        scope=payload.scope,
        tag=payload.tag,
        source_id=payload.source_id,
        offset=payload.offset,
        limit=payload.limit,
        allow_r18=allow_r18,
        allow_all_ages=allow_all_ages,
    )
    result["hits"] = await _filter_visible_hits(user, db, result["hits"])
    # Admins can use the complete index count; private users must not see
    # counts for books hidden by visibility rules.
    if user.role not in ("admin", "super_admin"):
        result["total"] = len(result["hits"])
    return SearchResult(**result)


@router.get("/index/stats")
async def index_stats(user: User = Depends(get_current_user)):
    """Get Meilisearch index statistics."""
    return search_service.get_index_stats()


@router.post("/index/rebuild")
async def rebuild_index(user: User = Depends(get_current_user)):
    """Rebuild search indexes from database."""
    try:
        result = await search_service.rebuild_index()
        return {"status": "ok", **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
