from fastapi import APIRouter

from app.api.routes import books, chapters, progress, sources, sync


router = APIRouter(prefix="/api")
router.include_router(books.router)
router.include_router(chapters.router)
router.include_router(progress.router)
router.include_router(sources.router)
router.include_router(sync.router)


@router.get("/test")
async def test():
    return {"message": "NovelHub API"}
