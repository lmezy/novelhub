from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.services.search import search_service


class SearchResult(BaseModel):
    hits: list[dict]
    total: int
    offset: int
    limit: int


router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchResult)
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    scope: str = Query("books", pattern="^(books|chapters)$"),
    offset: int = 0,
    limit: int = 20,
):
    if scope == "books":
        result = search_service.search_books(q, offset=offset, limit=limit)
    else:
        result = search_service.search_chapters(q, offset=offset, limit=limit)

    return SearchResult(
        hits=result["hits"],
        total=result.get("estimatedTotalHits", 0),
        offset=result.get("offset", offset),
        limit=result.get("limit", limit),
    )
