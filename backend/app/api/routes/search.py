from fastapi import APIRouter, Depends, HTTPException, Query
from app.models import User
from app.services.auth import get_current_user
from pydantic import BaseModel

from app.services.search import search_service


class SearchResult(BaseModel):
    hits: list[dict]
    total: int
    offset: int
    limit: int


router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchResult)
async def search(user: User = Depends(get_current_user),
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
