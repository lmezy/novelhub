from fastapi import APIRouter, Depends, HTTPException
from fastapi import BackgroundTasks
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import SessionLocal, get_db
from app.models import Book, BookCategory, Category, User
from app.repositories.category import CategoryRepository
from app.schemas.category import BookCategoryAssign, CategoryCreate, CategoryOut
from app.services.auth import get_current_user, require_admin
from app.services.auto_categorize import DEFAULT_CATEGORY_RULES, AutoCategorizationService
from app.services.book_kind import KINDS
from app.services.search import search_service
from app.services.visibility import apply_book_visibility, can_view_r18, ensure_book_visible

router = APIRouter(prefix="/categories", tags=["categories"])


async def _refresh_search_categories(db: AsyncSession, book_ids: list[str]) -> None:
    if not book_ids:
        return
    try:
        await search_service.sync_book_categories(db, book_ids)
    except Exception as exc:
        logger.warning("Could not refresh book categories in search: {}", exc)


async def _refresh_deleted_category(book_ids: list[str]) -> None:
    async with SessionLocal() as db:
        await _refresh_search_categories(db, book_ids)


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    kind: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List categories; ``kind`` keeps only genres holding that kind of book.

    The novel and comic pages pass their own ``kind`` so their category bar
    never offers a genre that only exists on the other page.
    """
    repo = CategoryRepository(db)
    value = str(kind or "").strip().lower()
    if value in KINDS:
        query = (
            select(Category)
            .join(BookCategory, BookCategory.category_id == Category.id)
            .join(Book, Book.id == BookCategory.book_id)
            .where(Book.kind == value)
        )
        query = apply_book_visibility(query, user)
        if not can_view_r18(user):
            query = query.where(Category.is_r18 == False)
        rows = await db.scalars(query.distinct().order_by(Category.name))
        return list(rows)
    return await repo.list_all(include_r18=can_view_r18(user))


@router.post("", response_model=CategoryOut, status_code=201, dependencies=[Depends(require_admin)])
async def create_category(payload: CategoryCreate, db: AsyncSession = Depends(get_db)):
    repo = CategoryRepository(db)
    existing = await repo.get_by_name(payload.name)
    if existing:
        raise HTTPException(status_code=409, detail="Category already exists")
    try:
        return await repo.create(
            name=payload.name,
            description=payload.description,
            color=payload.color,
            is_r18=payload.is_r18,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Category already exists") from exc


@router.delete("/{category_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_category(category_id: str, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    repo = CategoryRepository(db)
    affected = await repo.delete(category_id, DEFAULT_CATEGORY_RULES)
    if affected is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if affected:
        background_tasks.add_task(_refresh_deleted_category, affected)


@router.get("/book/{book_id}", response_model=list[CategoryOut])
async def get_book_categories(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    repo = CategoryRepository(db)
    return await repo.get_book_categories(book_id, include_r18=can_view_r18(user))


@router.put("/book/{book_id}", response_model=list[CategoryOut])
async def set_book_categories(book_id: str, payload: BookCategoryAssign, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    repo = CategoryRepository(db)
    await db.refresh(book, attribute_names=["categories_manually_set"], with_for_update=True)
    query = select(Category.id).where(Category.id.in_(payload.category_ids))
    if not book.is_r18:
        query = query.where(Category.is_r18 == False)
    allowed_ids = list(await db.scalars(query))
    book.categories_manually_set = True
    await repo.set_book_categories(book_id, allowed_ids)
    await _refresh_search_categories(db, [book_id])
    return await repo.get_book_categories(book_id, include_r18=can_view_r18(user))


@router.post("/auto", status_code=200, dependencies=[Depends(require_admin)])
async def auto_categorize_all(db: AsyncSession = Depends(get_db)):
    """Auto-categorize all books based on their tags."""
    return await AutoCategorizationService.categorize_all_books(db)


@router.post("/auto/{book_id}", status_code=200, dependencies=[Depends(require_admin)])
async def auto_categorize_book(book_id: str, db: AsyncSession = Depends(get_db)):
    """Auto-categorize a single book based on its tags."""
    cats = await AutoCategorizationService.categorize_book(db, book_id, reset_manual=True)
    await _refresh_search_categories(db, [book_id])
    return {"book_id": book_id, "categories": cats}
