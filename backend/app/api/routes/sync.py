from fastapi import APIRouter, Depends, HTTPException
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Source
from app.schemas.sync import (
    BookshelfSyncRequest,
    BookshelfSyncResult,
    DiscoverRequest,
    DiscoverResult,
    LocalContentRequest,
    LocalContentResult,
    LocalDirectResult,
    LocalImportRequest,
    LocalImportResult,
    LocalScanRequest,
    LocalScanResult,
    SyncRequest,
    SyncResult,
)
from app.services.auth import require_admin
from app.services.local_library import parse_local_book, scan_local_library
from app.services.sync import SyncService

router = APIRouter(prefix="/sync", tags=["sync"], dependencies=[Depends(require_admin)])


async def _ensure_local_source(db: AsyncSession) -> str:
    source = await db.get(Source, "local_markdown")
    if source is None or source.plugin_name != "local_markdown":
        source = Source(
            id="local_markdown",
            name="Local Markdown",
            plugin_name="local_markdown",
            enabled=True,
        )
        db.add(source)
        await db.commit()
    return "local_markdown"


def _to_file_url(path: str) -> str:
    path = path.strip()
    return path if path.startswith("file://") else f"file://{path}"


@router.post("/local", response_model=SyncResult)
async def import_local_book(
    payload: LocalImportRequest,
    db: AsyncSession = Depends(get_db),
):
    source_id = await _ensure_local_source(db)
    path = payload.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="Local path is required")
    try:
        return await SyncService(db).sync_book(source_id, _to_file_url(path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/local/scan", response_model=LocalScanResult)
async def scan_local_library_endpoint(payload: LocalScanRequest):
    try:
        books = scan_local_library(payload.path, max_depth=payload.max_depth)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LocalScanResult(root=payload.path, books=books)


@router.post("/local/direct", response_model=LocalDirectResult)
async def direct_local_books(payload: LocalImportRequest):
    paths = payload.book_paths or [payload.path]
    if not paths or not paths[0].strip():
        raise HTTPException(status_code=400, detail="Local path is required")
    books = []
    try:
        for path in paths:
            books.append(parse_local_book(path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LocalDirectResult(books=books)


@router.post("/local/content", response_model=LocalContentResult)
async def read_local_content(payload: LocalContentRequest):
    path = Path(payload.path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in (".md", ".txt"):
        raise HTTPException(status_code=400, detail="Chapter file not found")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    parts = text.split("\n", 1)
    title = parts[0].lstrip("#").strip() or path.stem
    content = parts[1] if len(parts) > 1 else ""
    return LocalContentResult(path=str(path), title=title, content=content)


@router.post("/local/import", response_model=LocalImportResult)
async def import_local_books(
    payload: LocalImportRequest,
    db: AsyncSession = Depends(get_db),
):
    source_id = await _ensure_local_source(db)
    paths = payload.book_paths or [payload.path]
    if not paths or not paths[0].strip():
        raise HTTPException(status_code=400, detail="Local path is required")

    results = []
    for path in paths:
        try:
            result = await SyncService(db).sync_book(source_id, _to_file_url(path))
            results.append({
                "path": path,
                "status": "ok",
                "book_id": result["book_id"],
                "created_chapters": result.get("created_chapters", 0),
                "skipped_chapters": result.get("skipped_chapters", 0),
            })
        except Exception as exc:
            await db.rollback()
            results.append({
                "path": path,
                "status": "failed",
                "error": str(exc),
            })
    return LocalImportResult(results=results)


@router.post("/book", response_model=SyncResult)
async def sync_book(payload: SyncRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).sync_book(payload.source_id, payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bookshelf", response_model=BookshelfSyncResult)
async def sync_bookshelf(payload: BookshelfSyncRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).sync_bookshelf(payload.source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/discover", response_model=DiscoverResult)
async def discover_and_sync(payload: DiscoverRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).discover_and_sync(
            source_id=payload.source_id,
            url=payload.url,
            page=payload.page,
            sync=payload.sync,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
