from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from uuid import uuid4

from loguru import logger
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import User
from app.services.security import hash_password

from app.api.router import router as api_router
from app.core.config import settings
from app.core.logging import setup_logging
# RateLimitMiddleware disabled due to asyncpg/greenlet event-loop conflict
# from app.core.rate_limit import RateLimitMiddleware
from app.core.error_handlers import generic_exception_handler, value_error_handler

setup_logging()

app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)

# app.add_middleware(RateLimitMiddleware)  # disabled: causes greenlet event-loop conflict with asyncpg
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(Exception, generic_exception_handler)
app.add_exception_handler(ValueError, value_error_handler)
app.add_exception_handler(
    RequestValidationError,
    lambda req, exc: JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    ),
)

app.include_router(api_router)




@app.on_event("startup")
async def seed_default_super_admin():
    async with SessionLocal() as db:
        result = await db.scalar(select(User).where(User.role == "super_admin"))
        if result is None:
            user = User(
                id=str(uuid4()),
                username="admin",
                email="admin@novelhub.local",
                password_hash=hash_password("admin"),
                role="super_admin",
            )
            db.add(user)
            await db.commit()
            logger.info("Default super admin created: admin / admin")
        else:
            logger.info("Super admin already exists, skipping seed.")


@app.get("/")
async def root():
    return {"project": settings.PROJECT_NAME, "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
