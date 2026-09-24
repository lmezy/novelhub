from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Literal
from loguru import logger
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
    # ``/novels`` and ``/comics`` search their own half of the library; the
    # index used to ignore the distinction and returned both kinds together.
    kind: Literal["novel", "comic"] | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


router = APIRouter(prefix="/search", tags=["search"])


async def _load_book_meta(
    db: AsyncSession,
    book_ids: set[str],
) -> dict[str, tuple[str | None, bool, str | None, str | None]]:
    """``book_id -> (owner_id, is_public, cover, display_cover)`` for one page.

    One query serves both jobs the route needs: the visibility decision and the
    cover the result list draws.  The cover deliberately does *not* live in the
    search index -- a re-sync or a user-chosen cover would then need an index
    write, and ``PUT /books/{id}/cover`` does not do one, so the index would go
    stale.  The page is at most ``limit`` hits, so this is a single small query.
    """
    if not book_ids:
        return {}
    try:
        rows = await db.execute(
            select(Book.id, Book.owner_id, Book.is_public, Book.cover, Book.display_cover)
            .where(Book.id.in_(book_ids))
        )
    except Exception as exc:
        # A cover is decoration: losing it must never turn a working search into
        # a 500, so a database hiccup just serves the page without pictures.
        logger.warning("Search cover lookup failed: {}", exc)
        return {}
    return {
        str(book_id): (owner_id, bool(is_public), cover, display_cover)
        for book_id, owner_id, is_public, cover, display_cover in rows.all()
    }


def _cover_path(book_id: str, meta: tuple | None) -> str | None:
    """The URL a search result shows for a book's cover, or ``None``.

    Same rule as ``books._book_cover_value``: a user-chosen cover wins, a remote
    URL is used as-is, and a locally stored file is reached through the
    ``/api/books/{id}/cover`` route.  Unlike ``_book_cover_value`` this returns
    ``None`` when the book has no cover at all, so the list can hide the slot
    instead of pointing an ``<img>`` at a guaranteed 404.
    """
    if meta is None:
        return None
    cover = meta[3] or meta[2]
    if not cover:
        return None
    if cover.startswith(("http://", "https://", "data:", "/api/books/")):
        return cover
    return f"/api/books/{book_id}/cover"


def _with_cover(hit: dict, meta: tuple | None) -> dict:
    """Attach the display cover to one hit (books and chapters alike).

    A chapter hit reaches its book through ``book_id``, and the cover lives on
    the book, so both types of result end up with a picture of the same book.
    """
    book_id = str(hit.get("book_id") or hit.get("id") or "")
    cover = _cover_path(book_id, meta)
    if cover is None:
        return hit
    return {**hit, "cover": cover, "cover_url": cover}


async def _filter_visible_hits(
    user: User,
    db: AsyncSession,
    hits: list[dict],
) -> list[dict]:
    book_ids = {
        str(hit.get("book_id") or hit.get("id") or "")
        for hit in hits
    }
    book_ids.discard("")
    meta_map = await _load_book_meta(db, book_ids)
    visible = [
        hit
        for hit in hits
        if user.role in ("admin", "super_admin")
        or _is_visible_book(
            meta_map.get(str(hit.get("book_id") or hit.get("id") or "")),
            user.id,
        )
    ]
    return [
        _with_cover(hit, meta_map.get(str(hit.get("book_id") or hit.get("id") or "")))
        for hit in visible
    ]


def _is_visible_book(meta: tuple | None, user_id: str) -> bool:
    if meta is None:
        return False
    owner_id, is_public = meta[0], meta[1]
    return owner_id is None or owner_id == user_id or is_public


@router.get("", response_model=SearchResult)
async def search(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    q: str = Query(default="", description="Search query"),
    scope: str = Query("books", pattern="^(books|chapters)$"),
    tag: str | None = Query(default=None, description="Book tag name"),
    kind: str | None = Query(default=None, description="novel | comic"),
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
            kind=kind,
        )
    else:
        result = search_service.search_chapters(
            q,
            offset=offset,
            limit=limit,
            allow_r18=allow_r18,
            allow_all_ages=allow_all_ages,
            tag=tag,
            kind=kind,
        )

    hits = await _filter_visible_hits(user, db, result["hits"])
    # Same rule as ``/advanced``: the count is everybody's, so a non-admin client
    # still knows there is a page 2 (see the comment there).
    return SearchResult(
        hits=hits,
        total=int(result.get("total", result.get("estimatedTotalHits", len(hits)))),
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
        kind=payload.kind,
        offset=payload.offset,
        limit=payload.limit,
        allow_r18=allow_r18,
        allow_all_ages=allow_all_ages,
    )
    result["hits"] = await _filter_visible_hits(user, db, result["hits"])
    # The total is served to everyone.  It used to be blanked to ``len(hits)``
    # for non-admins, to avoid counting books their visibility rules hide -- but
    # a total that can never exceed one page turns paging off for exactly the
    # users who need it ("下一页" was disabled forever and the page counter said
    # "1 / 1").  The count itself reveals nothing about *which* books are hidden,
    # and the sum of visible + hidden hits is a closer answer than "however many
    # happened to fit on this page".
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


@router.post("/index/kinds")
async def sync_index_kinds(user: User = Depends(get_current_user)):
    """Copy ``books.kind`` into the books index (novel/comic search filter).

    Runs automatically on startup when the index still lacks the field; this
    endpoint is the manual escape hatch after a re-classification.
    """
    try:
        return {"status": "ok", **await search_service.sync_book_kinds(force=True)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
