from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.opt(exception=exc).error("Unhandled exception on {} {}", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    logger.warning("ValueError on {} {}: {}", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
    )
