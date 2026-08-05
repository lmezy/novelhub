from fastapi import APIRouter

from app.api.routes import admin, ai, auth, backup, books, bookshelf, chapters, cookies, crawl, credentials, custom_tags, health, manual_login, progress, rag, search, source_changes, sources, sync, tags, tokens, yuedu
from app.api.routes import categories


router = APIRouter(prefix="/api")
router.include_router(admin.router)
router.include_router(ai.router)
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(backup.router)
router.include_router(books.router)
router.include_router(bookshelf.router)
router.include_router(chapters.router)
router.include_router(cookies.router)
router.include_router(crawl.router)
router.include_router(custom_tags.router)
router.include_router(progress.router)
router.include_router(rag.router)
router.include_router(search.router)
router.include_router(source_changes.router)
router.include_router(sources.router)
router.include_router(sync.router)
router.include_router(tags.router)
router.include_router(credentials.router)
router.include_router(tokens.router)
router.include_router(yuedu.router)
router.include_router(categories.router)

router.include_router(manual_login.router)

@router.get("/test")
async def test():
    return {"message": "NovelHub API"}
