from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.crawler.registry import get_plugin
from app.models import Source, Book, Cookie, CrawlTask, CrawlLog, User
from app.models.source_credential import SourceCredential
from app.schemas.source import (
    RemoteBookSearchOut,
    RemoteBookSearchResult,
    SourceCreate,
    SourceOut,
    SourceUpdate,
)
from app.services.auth import get_current_user, require_admin
from app.services.book_cleanup import delete_books
from app.services.search import search_service
from app.services.visibility import can_view_all_ages, can_view_r18


router = APIRouter(prefix="/sources", tags=["sources"])


def _can_view_source(user: User, source: Source) -> bool:
    if user.role in ("admin", "super_admin"):
        return True
    return source.owner_id is not None and source.owner_id == user.id


def _can_edit_source(user: User, source: Source) -> bool:
    if user.role in ("admin", "super_admin"):
        return True
    return source.owner_id is not None and source.owner_id == user.id


@router.get("", response_model=list[SourceOut])
async def list_sources(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = select(Source).order_by(Source.name.asc())
    conditions = []
    if user.role not in ("admin", "super_admin"):
        conditions.append(Source.owner_id == user.id)
    if can_view_all_ages(user):
        conditions.append(Source.is_r18 == False)
    if can_view_r18(user):
        conditions.append(Source.is_r18 == True)
    query = query.where(and_(*conditions)) if conditions else query.where(Source.id == "__none__")
    result = await db.scalars(query)
    return list(result)


@router.post("", response_model=SourceOut, status_code=201)
async def create_source(
    payload: SourceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = payload.scope or "personal"
    if scope == "global" and user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can create global sources")
    owner_id = None if scope == "global" else user.id
    source_id = payload.id
    if owner_id and await db.get(Source, source_id):
        source_id = f"user:{user.id}:{source_id}"
    if await db.get(Source, source_id):
        raise HTTPException(status_code=409, detail="Source already exists")
    data = payload.model_dump(mode="json", exclude={"scope", "id"})
    source = Source(id=source_id, owner_id=owner_id, **data)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


@router.put("/{source_id}", response_model=SourceOut, dependencies=[Depends(require_admin)])
async def update_source(
    source_id: str,
    payload: SourceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if not _can_edit_source(user, source):
        raise HTTPException(status_code=403, detail="Cannot edit this source")

    old_is_r18 = source.is_r18
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is None and key in ("enabled", "is_r18", "name", "plugin_name"):
            continue
        setattr(source, key, value)

    if data.get("is_r18") is not None and data["is_r18"] != old_is_r18:
        await db.execute(
            update(Book)
            .where(Book.source_id == source_id)
            .values(is_r18=data["is_r18"])
        )

    await db.commit()
    await db.refresh(source)

    if data.get("is_r18") is not None and data["is_r18"] != old_is_r18:
        books = (
            await db.scalars(
                select(Book)
                .options(selectinload(Book.tags), selectinload(Book.author))
                .where(Book.source_id == source_id)
            )
        ).unique().all()
        for book in books:
            try:
                search_service.index_book({
                    "id": book.id,
                    "title": book.title,
                    "author": book.author_name or "",
                    "description": book.description or "",
                    "status": book.status or "",
                    "source_id": book.source_id or "",
                    "author_id": book.author_id or "",
                    "is_r18": book.is_r18,
                    "tags": list(book.tag_names),
                    "category_names": list(book.category_names),
                })
            except Exception:
                continue

    return source


@router.get("/{source_id}/search", response_model=RemoteBookSearchOut)
async def search_remote_books(
    source_id: str,
    q: str,
    page: int = 1,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search a book source using its imported rules (YueDu/Legado)."""
    query = (q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    source = await db.get(Source, source_id)
    if source is None or not source.enabled:
        raise HTTPException(status_code=404, detail="Source not found or disabled")
    if not _can_view_source(user, source):
        raise HTTPException(status_code=404, detail="Source not found or disabled")
    if (
        (source.is_r18 and not can_view_r18(user))
        or (not source.is_r18 and not can_view_all_ages(user))
    ):
        raise HTTPException(status_code=404, detail="Source not found or disabled")

    config = source.config if source.plugin_name == "yuedu" else None
    plugin = get_plugin(source.plugin_name, config=config)
    if not hasattr(plugin, "search_books"):
        raise HTTPException(
            status_code=400,
            detail="Source does not support remote book search",
        )

    page = max(1, page)
    limit = min(max(1, limit), 100)
    try:
        items = await plugin.search_books(query, page=page, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing_map: dict[str, str] = {}
    candidates = set()
    for item in items:
        url = str(item.get("bookUrl") or item.get("url") or "").strip()
        last = url.rstrip("/").split("/")[-1] if "/" in url else url
        if url:
            candidates.add(url)
        if last:
            candidates.add(last)

    if candidates:
        rows = await db.execute(
            select(Book.id, Book.source_book_id).where(
                Book.source_id == source_id,
                Book.source_book_id.in_(list(candidates)),
            )
        )
        existing_map = {
            source_book_id: book_id
            for book_id, source_book_id in rows
            if source_book_id
        }

    results = []
    for item in items:
        url = str(item.get("bookUrl") or item.get("url") or "").strip()
        last = url.rstrip("/").split("/")[-1] if "/" in url else url
        book_id = existing_map.get(url) or existing_map.get(last)
        results.append(RemoteBookSearchResult(
            source_id=source.id,
            source_name=source.name,
            name=str(item.get("name") or "").strip() or "Unknown",
            author=str(item.get("author") or "").strip() or "Unknown",
            url=url,
            cover_url=item.get("coverUrl"),
            intro=item.get("intro"),
            kind=item.get("kind"),
            latest_chapter=item.get("lastChapter"),
            word_count=item.get("wordCount"),
            in_library=book_id is not None,
            book_id=book_id,
        ))

    return RemoteBookSearchOut(
        source_id=source.id,
        source_name=source.name,
        query=query,
        page=page,
        total=len(results),
        results=results,
    )


@router.delete("/{source_id}", status_code=200)
async def delete_source(
    source_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if not _can_edit_source(user, source):
        raise HTTPException(status_code=403, detail="Cannot delete this source")

    # Count related books
    book_ids = list(
        await db.scalars(select(Book.id).where(Book.source_id == source_id))
    )
    deleted_books = len(book_ids)
    if book_ids:
        await delete_books(db, book_ids, commit=False)

    # Delete related cookies
    await db.execute(delete(Cookie).where(Cookie.source == source_id))

    # Delete related credentials
    await db.execute(delete(SourceCredential).where(SourceCredential.source == source_id))

    # Delete related crawl logs (via task_id join), then crawl tasks
    await db.execute(
        delete(CrawlLog).where(
            CrawlLog.task_id.in_(
                select(CrawlTask.id).where(CrawlTask.source == source_id)
            )
        )
    )
    await db.execute(delete(CrawlTask).where(CrawlTask.source == source_id))

    # Delete the source itself
    await db.delete(source)
    await db.commit()

    return {
        "status": "ok",
        "deleted": source_id,
        "name": source.name,
        "deleted_books": deleted_books,
    }
