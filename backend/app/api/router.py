from fastapi import APIRouter

from app.api.routes import ai, auth, backup, books, chapters, cookies, crawl, health, progress, rag, search, sources, sync, tags, tokens


router = APIRouter(prefix="/api")
router.include_router(ai.router)
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(backup.router)
router.include_router(books.router)
router.include_router(chapters.router)
router.include_router(cookies.router)
router.include_router(crawl.router)
router.include_router(progress.router)
router.include_router(rag.router)
router.include_router(search.router)
router.include_router(sources.router)
router.include_router(sync.router)
router.include_router(tags.router)
router.include_router(tokens.router)


@router.get("/test")
async def test():
    return {"message": "NovelHub API"}
