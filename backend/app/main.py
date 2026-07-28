from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import router as api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.rate_limit import RateLimitMiddleware
from app.core.error_handlers import generic_exception_handler, value_error_handler

setup_logging()

app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)

app.add_middleware(RateLimitMiddleware)
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
        content={"detail": exc.errors()},
    ),
)

app.include_router(api_router)


@app.get("/")
async def root():
    return {"project": settings.PROJECT_NAME, "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
