import re
from uuid import uuid4
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models import Book, BookCategory, BookFavorite, BookFavoriteGroup, BookTag, Category, Chapter, Source, Tag, User
from app.services.book_enrichment import analyze_book_text
from app.services.auth import get_current_user, require_admin
from app.services.book_cleanup import delete_books
from app.services.bookshelf import favorite_group_ids_by_book
from app.services.custom_tags import list_book_custom_tags_map
from app.services.epub import EpubService
from app.services.local_file_parser import split_text_chapters
from app.services.search import search_service
from app.services.storage import BookStorage
from app.services.sync import SyncService
from app.services.visibility import (
    can_view_r18,
    can_view_all_ages,
    ensure_book_visible,
    visible_tags,
)
from app.schemas.book import (
    BookCreate,
    BookHomeOut,
    BookHomeSectionOut,
    BookOut,
    BookPageOut,
    BookSourceAlternate,
    BookSourceAlternatesOut,
    ManualAnalyzeRequest,
    ManualAnalyzeResult,
    ManualBookCreate,
)
from app.services.manual_import import ManualImportService


router = APIRouter(prefix="/books", tags=["books"])


class BatchDeleteRequest(BaseModel):
    ids: list[str]


class BatchFavoriteRequest(BaseModel):
    ids: list[str]


class BatchDeleteBySourceRequest(BaseModel):
    source_id: str


class BatchDeleteByCategoryRequest(BaseModel):
    category_id: str


class SetBookCoverRequest(BaseModel):
    source_book_id: str | None = None
    cover: str | None = None


class PublishBookRequest(BaseModel):
    confirm_all_ages: bool = False


def _normalize_book_title(title: str) -> str:
    return re.sub(
        r"[\s《》「」『』〈〉（）【】\[\]\"'“”‘’]+",
        "",
        title or "",
    ).lower()


def _book_cover_value(book: Book) -> str | None:
    """Return the display cover, preferring the user-selected cover."""
    cover = getattr(book, "display_cover", None) or getattr(book, "cover", None)
    if not cover:
        return None
    if cover.startswith(("http://", "https://", "data:", "/api/books/")):
        return cover
    return f"/api/books/{book.id}/cover"


def _serialize_book(
    book: Book,
    user: User,
    is_favorite: bool = False,
    custom_tags: dict[str, list[dict]] | None = None,
    shelf_group_ids: dict[str, list[str]] | None = None,
) -> BookOut:
    is_admin = user.role in ("admin", "super_admin")
    cover = _book_cover_value(book)
    if can_view_r18(user):
        category_names = [bc.category.name for bc in book.categories if bc.category]
    else:
        category_names = [
            bc.category.name
            for bc in book.categories
            if bc.category and not bc.category.is_r18
        ]
    return BookOut(
        id=book.id,
        title=book.title,
        author_id=book.author_id,
        source_id=book.source_id,
        source_book_id=book.source_book_id,
        cover=cover,
        description=book.description,
        status=book.status,
        is_r18=book.is_r18 if (is_admin or book.owner_id == user.id) else False,
        owner_id=(
            book.owner_id
            if is_admin or book.owner_id == user.id
            else None
        ),
        is_public=book.is_public,
        all_ages_confirmed=book.all_ages_confirmed,
        is_favorite=is_favorite,
        created_at=book.created_at,
        updated_at=book.updated_at,
        tag_names=visible_tags(user, book.tag_names),
        category_names=category_names,
        author_name=book.author_name,
        custom_tags=(custom_tags or {}).get(book.id, []),
        shelf_group_ids=(shelf_group_ids or {}).get(book.id, []),
    )


def _apply_book_visibility(query, user: User):
    if user.role not in ("admin", "super_admin"):
        query = query.where(or_(
            Book.owner_id.is_(None),
            Book.owner_id == user.id,
            Book.is_public == True,
        ))
    conditions = []
    if can_view_all_ages(user):
        conditions.append(Book.is_r18 == False)
    if can_view_r18(user):
        conditions.append(Book.is_r18 == True)
    return query.where(or_(*conditions)) if conditions else query.where(Book.id == "__none__")


async def _serialize_books(db: AsyncSession, books: list[Book], user: User) -> list[BookOut]:
    if not books:
        return []
    book_ids = [book.id for book in books]
    favorite_ids = set(
        await db.scalars(
            select(BookFavorite.book_id).where(
                BookFavorite.user_id == user.id,
                BookFavorite.book_id.in_(book_ids),
            )
        )
    )
    custom_tags = await list_book_custom_tags_map(db, book_ids, user)
    return [
        _serialize_book(book, user, book.id in favorite_ids, custom_tags)
        for book in books
    ]


@router.get("", response_model=list[BookOut])
async def list_books(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = _apply_book_visibility(
        select(Book).options(selectinload(Book.tags), selectinload(Book.categories)),
        user,
    ).order_by(Book.updated_at.desc())
    result = await db.scalars(query)
    books = list(result.unique().all()) if hasattr(result, "unique") else list(result)
    return await _serialize_books(db, books, user)


@router.get("/home", response_model=BookHomeOut)
async def get_books_home(
    section_limit: int = Query(6, ge=1, le=12),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    total = int(await db.scalar(_apply_book_visibility(
        select(func.count()).select_from(Book), user
    )) or 0)
    latest_rows = await db.scalars(
        _apply_book_visibility(
            select(Book).options(selectinload(Book.tags), selectinload(Book.categories)),
            user,
        ).order_by(Book.updated_at.desc()).limit(section_limit)
    )
    latest_books = list(latest_rows.unique().all())

    category_query = select(Category).order_by(Category.name)
    if not can_view_r18(user):
        category_query = category_query.where(Category.is_r18 == False)
    categories = list((await db.scalars(category_query)).all())
    section_rows: list[tuple[Category, int, list[Book]]] = []
    all_books = list(latest_books)
    for category in categories:
        count_query = _apply_book_visibility(
            select(func.count()).select_from(Book).join(
                BookCategory, BookCategory.book_id == Book.id
            ).where(BookCategory.category_id == category.id),
            user,
        )
        category_total = int(await db.scalar(count_query) or 0)
        if not category_total:
            continue
        rows = await db.scalars(
            _apply_book_visibility(
                select(Book)
                .join(BookCategory, BookCategory.book_id == Book.id)
                .options(selectinload(Book.tags), selectinload(Book.categories))
                .where(BookCategory.category_id == category.id),
                user,
            ).order_by(Book.updated_at.desc()).limit(section_limit)
        )
        books = list(rows.unique().all())
        section_rows.append((category, category_total, books))
        all_books.extend(books)

    serialized = await _serialize_books(
        db,
        list({book.id: book for book in all_books}.values()),
        user,
    )
    by_id = {book.id: book for book in serialized}
    return BookHomeOut(
        total=total,
        latest=[by_id[book.id] for book in latest_books if book.id in by_id],
        sections=[
            BookHomeSectionOut(
                category_id=category.id,
                category_name=category.name,
                category_color=category.color,
                total=category_total,
                books=[by_id[book.id] for book in books if book.id in by_id],
            )
            for category, category_total, books in section_rows
        ],
    )


@router.get("/browse", response_model=BookPageOut)
async def browse_books(
    category: str | None = None,
    source_id: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=48),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Book)
    count_query = select(func.count()).select_from(Book)
    if category:
        query = query.join(BookCategory, BookCategory.book_id == Book.id).join(
            Category, Category.id == BookCategory.category_id
        ).where(Category.name == category)
        count_query = count_query.join(
            BookCategory, BookCategory.book_id == Book.id
        ).join(Category, Category.id == BookCategory.category_id).where(
            Category.name == category
        )
    if source_id:
        query = query.where(Book.source_id == source_id)
        count_query = count_query.where(Book.source_id == source_id)
    total = int(await db.scalar(_apply_book_visibility(count_query, user)) or 0)
    rows = await db.scalars(
        _apply_book_visibility(
            query.options(selectinload(Book.tags), selectinload(Book.categories)),
            user,
        ).order_by(Book.updated_at.desc()).offset(offset).limit(limit)
    )
    books = list(rows.unique().all())
    return BookPageOut(
        items=await _serialize_books(db, books, user),
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/batch-delete", dependencies=[Depends(require_admin)])
async def batch_delete_books(
    payload: BatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No book ids provided")

    books = (
        await db.scalars(select(Book).where(Book.id.in_(payload.ids)))
    ).unique().all()
    if not books:
        raise HTTPException(status_code=404, detail="No books found")

    book_ids = [book.id for book in books]
    await delete_books(db, book_ids)
    return {"deleted": len(book_ids)}


@router.post("/batch-delete-by-source", dependencies=[Depends(require_admin)])
async def batch_delete_books_by_source(
    payload: BatchDeleteBySourceRequest,
    db: AsyncSession = Depends(get_db),
):
    source_id = payload.source_id.strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="Source id is required")
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    book_ids = list(
        await db.scalars(
            select(Book.id).where(Book.source_id == source_id)
        )
    )
    deleted = await delete_books(db, book_ids)
    return {"source_id": source_id, "deleted": deleted}


@router.post("/batch-delete-by-category", dependencies=[Depends(require_admin)])
async def batch_delete_books_by_category(
    payload: BatchDeleteByCategoryRequest,
    db: AsyncSession = Depends(get_db),
):
    category_id = payload.category_id.strip()
    if not category_id:
        raise HTTPException(status_code=400, detail="Category id is required")
    category = await db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    book_ids = list(await db.scalars(
        select(BookCategory.book_id).where(BookCategory.category_id == category_id)
    ))
    deleted = await delete_books(db, book_ids)
    return {
        "category_id": category_id,
        "category_name": category.name,
        "deleted": deleted,
    }


@router.post("/batch-favorite")
async def batch_favorite_books(
    payload: BatchFavoriteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No book ids provided")
    books = (
        await db.scalars(select(Book).where(Book.id.in_(payload.ids)))
    ).all()
    existing = set(
        await db.scalars(
            select(BookFavorite.book_id).where(
                BookFavorite.user_id == user.id,
                BookFavorite.book_id.in_(payload.ids),
            )
        )
    )
    added = 0
    for book in books:
        if not ensure_book_visible(user, book):
            continue
        if book.id in existing:
            continue
        db.add(BookFavorite(id=str(uuid4()), user_id=user.id, book_id=book.id))
        added += 1
    await db.commit()
    return {"added": added}


@router.post("/batch-unfavorite")
async def batch_unfavorite_books(
    payload: BatchFavoriteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No book ids provided")
    result = await db.execute(
        delete(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id.in_(payload.ids),
        )
    )
    await db.commit()
    return {"removed": result.rowcount}


@router.post("/manual", dependencies=[Depends(require_admin)])
async def create_manual_book(
    payload: ManualBookCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ManualImportService(db).import_book(
            title=payload.title,
            author=payload.author,
            description=payload.description,
            status=payload.status,
            tags=payload.tags,
            chapters=[chapter.model_dump() for chapter in payload.chapters],
            is_r18=payload.is_r18,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/manual/analyze",
    response_model=ManualAnalyzeResult,
    dependencies=[Depends(require_admin)],
)
async def analyze_manual_book(payload: ManualAnalyzeRequest):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    result = analyze_book_text(text, payload.filename, is_r18=payload.is_r18)
    return ManualAnalyzeResult(
        **result,
        chapter_count=len(split_text_chapters(text)),
    )


@router.post("", response_model=BookOut, status_code=201)
async def create_book(payload: BookCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = Book(id=str(uuid4()), **payload.model_dump())
    db.add(book)
    await db.commit()
    await db.refresh(book)
    return book


@router.get("/favorites", response_model=list[BookOut])
async def list_favorite_books(
    group_id: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Book)
        .join(BookFavorite, BookFavorite.book_id == Book.id)
        .where(BookFavorite.user_id == user.id)
        .options(selectinload(Book.tags))
        .order_by(BookFavorite.created_at.desc())
    )
    if group_id:
        query = query.join(
            BookFavoriteGroup,
            BookFavoriteGroup.favorite_id == BookFavorite.id,
        ).where(BookFavoriteGroup.group_id == group_id)
    if user.role not in ("admin", "super_admin"):
        query = query.where(or_(
            Book.owner_id.is_(None),
            Book.owner_id == user.id,
            Book.is_public == True,
        ))
    conditions = []
    if can_view_all_ages(user):
        conditions.append(Book.is_r18 == False)
    if can_view_r18(user):
        conditions.append(Book.is_r18 == True)
    query = query.where(or_(*conditions)) if conditions else query.where(Book.id == "__none__")
    result = await db.scalars(query)
    books = list(result)
    custom_tags = await list_book_custom_tags_map(
        db,
        [book.id for book in books],
        user,
    )
    shelf_groups = await favorite_group_ids_by_book(
        db,
        user.id,
        [book.id for book in books],
    )
    return [
        _serialize_book(book, user, True, custom_tags, shelf_groups)
        for book in books
    ]


@router.get("/{book_id}/cover")
async def get_book_cover(book_id: str, db: AsyncSession = Depends(get_db)):
    """Serve a locally stored cover image, or redirect to the remote URL."""
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Cover not found")
    cover_value = book.display_cover or book.cover
    if not cover_value:
        raise HTTPException(status_code=404, detail="Cover not found")
    if cover_value.startswith(("http://", "https://")):
        return RedirectResponse(cover_value)
    cover_path = Path(settings.STORAGE_PATH).parent / cover_value
    if not cover_path.is_file():
        raise HTTPException(status_code=404, detail="Cover not found")
    return FileResponse(cover_path)


@router.put("/{book_id}/cover")
async def set_book_cover(
    book_id: str,
    payload: SetBookCoverRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Choose a cover from another source book or a direct cover URL."""
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")

    if payload.cover is not None:
        cover = str(payload.cover).strip()
        book.display_cover = cover or None
    elif payload.source_book_id:
        source_book = await db.get(Book, payload.source_book_id)
        if source_book is None or not ensure_book_visible(user, source_book):
            raise HTTPException(status_code=404, detail="Source book not found")
        source_cover = source_book.display_cover or source_book.cover
        if not source_cover:
            raise HTTPException(status_code=400, detail="Source book has no cover")
        if source_cover.startswith(("http://", "https://", "data:")):
            book.display_cover = source_cover
        else:
            source_path = Path(settings.STORAGE_PATH).parent / source_cover
            if not source_path.is_file():
                raise HTTPException(status_code=400, detail="Source cover file not found")
            book.display_cover = BookStorage().save_display_cover(
                book.id,
                source_path.read_bytes(),
            )
    else:
        # Empty payload resets to the source cover.
        book.display_cover = None

    await db.commit()
    return {
        "book_id": book.id,
        "cover": _book_cover_value(book),
        "display_cover": book.display_cover,
    }


@router.post("/{book_id}/publish", response_model=BookOut)
async def publish_book(
    book_id: str,
    payload: PublishBookRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    if user.role not in ("admin", "super_admin") and book.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Cannot publish this book")
    if book.is_r18:
        raise HTTPException(status_code=400, detail="R18 books cannot be published as all-ages")
    if not payload.confirm_all_ages:
        raise HTTPException(status_code=400, detail="All-ages confirmation is required")

    from app.repositories.tag import TagRepository

    book.is_public = True
    book.all_ages_confirmed = True
    tag = await TagRepository(db).get_or_create("all-ages")
    existing_tag = await db.scalar(
        select(BookTag).where(
            BookTag.book_id == book.id,
            BookTag.tag_id == tag.id,
        )
    )
    if existing_tag is None:
        db.add(BookTag(book_id=book.id, tag_id=tag.id))
    await db.commit()
    book = await db.scalar(
        select(Book)
        .options(selectinload(Book.tags), selectinload(Book.categories))
        .where(Book.id == book.id)
    )
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    search_service.update_book_tags(book.id, list(book.tag_names))
    favorite = await db.scalar(
        select(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book.id,
        )
    )
    return _serialize_book(book, user, favorite is not None)


@router.delete("/{book_id}/publish", response_model=BookOut)
async def unpublish_book(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    if user.role not in ("admin", "super_admin") and book.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Cannot unpublish this book")

    from app.repositories.tag import TagRepository

    book.is_public = False
    book.all_ages_confirmed = False
    tag = await TagRepository(db).get_or_create("all-ages")
    await db.execute(
        delete(BookTag).where(
            BookTag.book_id == book.id,
            BookTag.tag_id == tag.id,
        )
    )
    await db.commit()
    book = await db.scalar(
        select(Book)
        .options(selectinload(Book.tags), selectinload(Book.categories))
        .where(Book.id == book.id)
    )
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    search_service.update_book_tags(book.id, list(book.tag_names))
    favorite = await db.scalar(
        select(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book.id,
        )
    )
    return _serialize_book(book, user, favorite is not None)


@router.post("/{book_id}/to-r18", response_model=BookOut)
async def convert_book_to_r18(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark an all-ages book as R18, unpublishing it in the process."""
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    if user.role not in ("admin", "super_admin") and book.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Cannot modify this book")

    from app.repositories.tag import TagRepository

    book.is_r18 = True
    book.is_public = False
    book.all_ages_confirmed = False
    tag_repo = TagRepository(db)
    all_ages_tag = await tag_repo.get_or_create("all-ages")
    await db.execute(
        delete(BookTag).where(
            BookTag.book_id == book.id,
            BookTag.tag_id == all_ages_tag.id,
        )
    )
    r18_tag = await tag_repo.get_or_create("r18")
    existing_r18 = await db.scalar(
        select(BookTag).where(
            BookTag.book_id == book.id,
            BookTag.tag_id == r18_tag.id,
        )
    )
    if existing_r18 is None:
        db.add(BookTag(book_id=book.id, tag_id=r18_tag.id))
    await db.commit()

    book = await db.scalar(
        select(Book)
        .options(selectinload(Book.tags), selectinload(Book.categories))
        .where(Book.id == book.id)
    )
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
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
    favorite = await db.scalar(
        select(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book.id,
        )
    )
    return _serialize_book(book, user, favorite is not None)


@router.delete("/{book_id}/tags", status_code=204, dependencies=[Depends(require_admin)])
async def remove_book_tag(
    book_id: str,
    tag_name: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Remove one source tag from a book without touching other books' tags."""
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    tag = await db.scalar(select(Tag).where(Tag.name == tag_name))
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    book_tag = await db.scalar(
        select(BookTag).where(
            BookTag.book_id == book_id,
            BookTag.tag_id == tag.id,
        )
    )
    if book_tag is None:
        raise HTTPException(status_code=404, detail="Tag not found on book")

    await db.delete(book_tag)
    remaining = await db.scalar(
        select(func.count()).select_from(BookTag).where(BookTag.tag_id == tag.id)
    )
    if not remaining:
        await db.delete(tag)
    await db.commit()

    remaining_names = [
        name
        for (name,) in (
            await db.execute(
                select(Tag.name)
                .join(BookTag, BookTag.tag_id == Tag.id)
                .where(BookTag.book_id == book_id)
            )
        ).all()
    ]
    search_service.update_book_tags(book_id, remaining_names)


@router.post("/{book_id}/favorite")
async def favorite_book(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    existing = await db.scalar(
        select(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book_id,
        )
    )
    if existing is None:
        db.add(BookFavorite(id=str(uuid4()), user_id=user.id, book_id=book_id))
        await db.commit()
    return {"favorited": True}


@router.delete("/{book_id}/favorite")
async def unfavorite_book(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    await db.execute(
        delete(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book_id,
        )
    )
    await db.commit()
    return {"favorited": False}


@router.get("/{book_id}", response_model=BookOut)
async def get_book(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    favorite = await db.scalar(
        select(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book_id,
        )
    )
    book.is_favorite = favorite is not None
    custom_tags = await list_book_custom_tags_map(db, [book.id], user)
    shelf_groups = await favorite_group_ids_by_book(db, user.id, [book.id])
    return _serialize_book(book, user, book.is_favorite, custom_tags, shelf_groups)


@router.get("/{book_id}/sources", response_model=BookSourceAlternatesOut)
async def list_book_sources(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List other library books with the same title across sources."""
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")

    query = select(Book).options(selectinload(Book.author)).where(Book.id != book_id)
    if user.role not in ("admin", "super_admin"):
        query = query.where(or_(
            Book.owner_id.is_(None),
            Book.owner_id == user.id,
            Book.is_public == True,
        ))
    conditions = []
    if can_view_all_ages(user):
        conditions.append(Book.is_r18 == False)
    if can_view_r18(user):
        conditions.append(Book.is_r18 == True)
    query = query.where(or_(*conditions)) if conditions else query.where(Book.id == "__none__")

    normalized = _normalize_book_title(book.title)
    candidates = [
        b
        for b in (await db.scalars(query)).unique().all()
        if _normalize_book_title(b.title) == normalized
    ]
    candidates.append(book)

    if not candidates:
        return BookSourceAlternatesOut(book_id=book_id, sources=[])

    chapter_counts = dict(
        (
            await db.execute(
                select(Chapter.book_id, func.count(Chapter.id))
                .where(Chapter.book_id.in_([b.id for b in candidates]))
                .group_by(Chapter.book_id)
            )
        ).all()
    )

    source_ids = {b.source_id for b in candidates if b.source_id}
    source_names: dict[str, str] = {}
    if source_ids:
        source_rows = await db.execute(
            select(Source.id, Source.name).where(Source.id.in_(source_ids))
        )
        source_names = dict(source_rows.all())

    sources = [
        BookSourceAlternate(
            id=b.id,
            source_id=b.source_id,
            source_name=source_names.get(b.source_id),
            source_book_id=b.source_book_id,
            title=b.title,
            author_name=b.author_name,
            cover=_book_cover_value(b),
            status=b.status,
            chapter_count=chapter_counts.get(b.id, 0),
            updated_at=b.updated_at,
            is_current=b.id == book.id,
        )
        for b in candidates
    ]
    sources.sort(
        key=lambda s: (
            not s.is_current,
            s.author_name != book.author_name,
            s.source_name or "",
        )
    )
    return BookSourceAlternatesOut(book_id=book_id, sources=sources)


@router.delete("/{book_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_book(book_id: str, db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    await delete_books(db, [book.id])
@router.get("/{book_id}/epub")
async def download_epub(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    try:
        epub_bytes = await EpubService(db).generate(book_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_name = book.title.replace("/", "_").replace("\\", "_")[:60]
    return Response(
        content=epub_bytes,
        media_type="application/epub+zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.epub"'},
    )
@router.post("/{book_id}/sync", dependencies=[Depends(require_admin)])
async def resync_book(book_id: str, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).resync_book(book_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


