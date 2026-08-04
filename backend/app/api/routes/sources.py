from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.crawler.registry import get_plugin
from app.models import Source, Book, Cookie, CrawlTask, CrawlLog, User
from app.models.source_credential import SourceCredential
from app.schemas.source import (
    RemoteBookSearchOut,
    RemoteBookSearchResult,
    SourceCreate,
    SourceOut,
)
from app.services.auth import get_current_user, require_admin
from app.services.book_cleanup import delete_books


router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[SourceOut])
async def list_sources(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.scalars(select(Source).order_by(Source.name.asc()))
    return list(result)


@router.post("", response_model=SourceOut, status_code=201, dependencies=[Depends(require_admin)])
async def create_source(payload: SourceCreate, db: AsyncSession = Depends(get_db)):
    if await db.get(Source, payload.id):
        raise HTTPException(status_code=409, detail="Source already exists")
    source = Source(**payload.model_dump(mode="json"))
    db.add(source)
    await db.commit()
    await db.refresh(source)
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


@router.delete("/{source_id}", status_code=200, dependencies=[Depends(require_admin)])
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)):
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

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
