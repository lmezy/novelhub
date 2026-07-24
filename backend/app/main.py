from fastapi import FastAPI

from app.api.router import router as api_router
from app.core.config import settings


app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)
app.include_router(api_router)


@app.get("/")
async def root():
    return {"project": settings.PROJECT_NAME, "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
