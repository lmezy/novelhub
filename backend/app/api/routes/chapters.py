from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, Chapter, User
from app.schemas.chapter import ChapterContentOut, ChapterOut
from app.services.auth import get_current_user, require_admin
from app.services.storage import BookStorage
from app.services.sync import SyncService
from app.services.visibility import ensure_book_visible


router = APIRouter(tags=["chapters"])


@router.get("/books/{book_id}/chapters", response_model=list[ChapterOut])
async def list_chapters(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    result = await db.scalars(
        select(Chapter)
        .where(Chapter.book_id == book_id)
        .order_by(Chapter.chapter_number.asc())
    )
    return list(result)


@router.get("/chapters/{chapter_id}", response_model=ChapterContentOut)
async def get_chapter(chapter_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    book = await db.get(Book, chapter.book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Chapter not found")
    try:
        content = BookStorage().read_chapter(chapter.content_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Chapter content not found")
    except OSError:
        content = ""
    return ChapterContentOut(
        id=chapter.id,
        title=chapter.title,
        chapter_number=chapter.chapter_number,
        source_chapter_id=chapter.source_chapter_id,
        content_path=chapter.content_path,
        content=content or "",
    )


@router.get("/chapters/{chapter_id}/content")
async def get_chapter_content_chunk(
    chapter_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200_000, ge=1, le=500_000),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a bounded chapter slice for very large one-chapter books.

    The normal chapter endpoint remains unchanged for exports and API
    compatibility. The reader uses this endpoint to avoid transferring and
    laying out an entire book-sized chapter before the first page is shown.
    """
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    book = await db.get(Book, chapter.book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Chapter not found")
    try:
        content = BookStorage().read_chapter(chapter.content_path) or ""
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Chapter content not found")
    except OSError:
        content = ""

    total = len(content)
    start = min(offset, total)
    end = min(start + limit, total)
    if end < total:
        # Avoid cutting directly through a paragraph when possible. The
        # reader can still make progress for a single enormous line.
        boundary = content.rfind("\n", start, end)
        if boundary > start + limit // 2:
            end = boundary + 1
    return {
        "id": chapter.id,
        "title": chapter.title,
        "chapter_number": chapter.chapter_number,
        "source_chapter_id": chapter.source_chapter_id,
        "content_path": chapter.content_path,
        "content": content[start:end],
        "offset": start,
        "next_offset": end if end < total else None,
        "total_length": total,
    }


@router.get("/chapters/{chapter_id}/images/{filename}")
async def get_chapter_image(
    chapter_id: str,
    filename: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    book = await db.get(Book, chapter.book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Chapter not found")
    try:
        path = BookStorage().chapter_image_path(book.id, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)


@router.post("/chapters/{chapter_id}/sync", dependencies=[Depends(require_admin)])
async def resync_chapter(chapter_id: str, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).resync_chapter(chapter_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
