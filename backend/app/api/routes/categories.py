from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models import User
from app.repositories.category import CategoryRepository
from app.schemas.category import BookCategoryAssign, CategoryCreate, CategoryOut
from app.services.auth import get_current_user, require_admin

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
async def list_categories(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    repo = CategoryRepository(db)
    return await repo.list_all()


@router.post("", response_model=CategoryOut, status_code=201, dependencies=[Depends(require_admin)])
async def create_category(payload: CategoryCreate, db: AsyncSession = Depends(get_db)):
    repo = CategoryRepository(db)
    existing = await repo.get_by_name(payload.name)
    if existing:
        raise HTTPException(status_code=409, detail="Category already exists")
    return await repo.create(name=payload.name, description=payload.description, color=payload.color)


@router.delete("/{category_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_category(category_id: str, db: AsyncSession = Depends(get_db)):
    repo = CategoryRepository(db)
    if not await repo.delete(category_id):
        raise HTTPException(status_code=404, detail="Category not found")


@router.get("/book/{book_id}", response_model=list[CategoryOut])
async def get_book_categories(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    repo = CategoryRepository(db)
    return await repo.get_book_categories(book_id)


@router.put("/book/{book_id}", response_model=list[CategoryOut])
async def set_book_categories(book_id: str, payload: BookCategoryAssign, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    repo = CategoryRepository(db)
    await repo.set_book_categories(book_id, payload.category_ids)
    return await repo.get_book_categories(book_id)


@router.post("/auto", status_code=200, dependencies=[Depends(require_admin)])
async def auto_categorize_all(db: AsyncSession = Depends(get_db)):
    """Auto-categorize all books based on their tags."""
    from app.services.auto_categorize import AutoCategorizationService
    return await AutoCategorizationService.categorize_all_books(db)


@router.post("/auto/{book_id}", status_code=200, dependencies=[Depends(require_admin)])
async def auto_categorize_book(book_id: str, db: AsyncSession = Depends(get_db)):
    """Auto-categorize a single book based on its tags."""
    from app.services.auto_categorize import AutoCategorizationService
    cats = await AutoCategorizationService.categorize_book(db, book_id)
    return {"book_id": book_id, "categories": cats}
