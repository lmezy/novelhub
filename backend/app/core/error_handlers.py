from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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

async def request_validation_handler(request: Request, exc: RequestValidationError):
    """Answer a malformed payload, and say *which* field was wrong in the log.

    Without this the access log only shows "422" and the failing field is
    invisible from the server side (the admin AI page looked like it silently
    refused to save four numbers).
    """
    try:
        body = await request.body()
        preview = body.decode("utf-8", "replace")[:800]
    except Exception:  # pragma: no cover - body already consumed
        preview = "<unavailable>"
    logger.warning(
        "422 on {} {}: {} | body={}",
        request.method,
        request.url.path,
        exc.errors()[:4],
        preview,
    )
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})
