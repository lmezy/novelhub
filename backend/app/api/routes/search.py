from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Literal
from app.models import User
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
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchResult)
async def search(user: User = Depends(get_current_user),
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

    return SearchResult(
        hits=result["hits"],
        total=result.get("estimatedTotalHits", 0),
        offset=result.get("offset", offset),
        limit=result.get("limit", limit),
    )


@router.post("/advanced", response_model=SearchResult)
async def advanced_search(
    payload: AdvancedSearchRequest,
    user: User = Depends(get_current_user),
):
    allow_r18 = can_view_r18(user)
    allow_all_ages = can_view_all_ages(user)
    result = search_service.advanced_search(
        [condition.model_dump() for condition in payload.conditions],
        match=payload.match,
        scope=payload.scope,
        tag=payload.tag,
        offset=payload.offset,
        limit=payload.limit,
        allow_r18=allow_r18,
        allow_all_ages=allow_all_ages,
    )
    return SearchResult(**result)


@router.get("/index/stats")
async def index_stats(user: User = Depends(get_current_user)):
    """Get Meilisearch index statistics."""
    return search_service.get_index_stats()


@router.post("/index/rebuild")
async def rebuild_index(user: User = Depends(get_current_user)):
    """Rebuild search indexes from database."""
    try:
        result = search_service.rebuild_index()
        return {"status": "ok", **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
