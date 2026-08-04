"""YueDu book source import API.

Endpoints:
- POST /api/yuedu/import   Import yuedu sources from URL or raw JSON
- GET  /api/yuedu/preview  Preview what sources a URL would import
"""

import json
import hashlib
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import CrawlTask, Source
from app.services.auth import require_admin
from app.services.proxy_config import get_proxy_config
from app.services.task_queue import enqueue_crawl_all

router = APIRouter(prefix="/yuedu", tags=["yuedu"])


async def _fetch_response(url: str, timeout: float = 30.0) -> httpx.Response:
    cfg = get_proxy_config()
    proxy_url = (cfg.https_proxy or cfg.http_proxy) if cfg.enabled else None
    proxies: list[str | None] = [None]
    if proxy_url:
        proxies.insert(0, proxy_url)

    last_error: httpx.HTTPError | None = None
    for proxy in proxies:
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                proxy=proxy,
                trust_env=False,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp
        except httpx.RequestError as exc:
            last_error = exc
            if proxy is None:
                raise
            logger.warning(
                "Configured proxy {} unreachable ({}); retrying direct",
                proxy_url,
                exc,
            )

    if last_error is not None:
        raise last_error
    raise httpx.ConnectError(f"Request failed for {url}")


class YueduImportRequest(BaseModel):
    url: str | None = None
    json_text: str | None = None
    is_r18: bool = False


class YueduImportResult(BaseModel):
    total: int
    imported: int
    skipped: int
    sources: list[dict[str, Any]]


@router.post("/import", response_model=YueduImportResult, dependencies=[Depends(require_admin)])
async def import_yuedu_sources(payload: YueduImportRequest, db: AsyncSession = Depends(get_db)):
    """Import YueDu book sources from a URL or raw JSON text."""
    sources_json: list[dict[str, Any]] = []

    if payload.json_text:
        try:
            parsed = json.loads(payload.json_text)
            sources_json = _extract_sources(parsed)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    elif payload.url:
        try:
            resp = await _fetch_response(payload.url)
            parsed = resp.json()
            sources_json = _extract_sources(parsed)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {e}")
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"URL returned invalid JSON: {e}")

    else:
        raise HTTPException(status_code=400, detail="Either url or json_text is required")

    if not sources_json:
        raise HTTPException(status_code=400, detail="No valid book sources found in the input")

    total = len(sources_json)
    imported = 0
    skipped = 0
    results: list[dict[str, Any]] = []

    for src in sources_json:
        name = src.get("bookSourceName", "Unknown")
        base_url = src.get("bookSourceUrl", "")
        source_id = _make_source_id(name, base_url)

        existing = await db.get(Source, source_id)
        if existing:
            skipped += 1
            results.append({"id": source_id, "name": name, "status": "skipped"})
            continue

        source = Source(
            id=source_id,
            name=name,
            url=base_url,
            plugin_name="yuedu",
            enabled=True,
            is_r18=payload.is_r18,
            config=src,
        )
        db.add(source)
        imported += 1
        results.append({"id": source_id, "name": name, "status": "imported", "bookshelf_url": None})

    await db.commit()

    # Bookshelf URLs are detected lazily during sync, so import stays fast.

    return YueduImportResult(
        total=total,
        imported=imported,
        skipped=skipped,
        sources=results,
    )


class YueduImportSyncRequest(BaseModel):
    url: str | None = None
    json_text: str | None = None
    is_r18: bool = False
    cookie: str | None = None
    discover: bool = True
    max_discover_pages: int = 3


class YueduImportSyncResult(BaseModel):
    sources_total: int
    sources_imported: int
    sources_skipped: int
    books_synced: int
    chapters_downloaded: int
    books_discovered: int
    errors: list[dict[str, Any]]
    details: list[dict[str, Any]]


class YueduImportTaskResult(BaseModel):
    tasks: list[dict[str, Any]]


@router.post("/import-task", response_model=YueduImportTaskResult, status_code=202, dependencies=[Depends(require_admin)])
async def import_yuedu_sources_as_tasks(
    payload: YueduImportSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    """Import sources and enqueue one crawl task per source for progress tracking."""
    from uuid import uuid4

    from app.models import Cookie
    from app.services.cookie_crypto import encrypt_cookie

    import_result = await import_yuedu_sources(
        YueduImportRequest(
            url=payload.url,
            json_text=payload.json_text,
            is_r18=payload.is_r18,
        ),
        db,
    )

    if payload.cookie and payload.cookie.strip():
        for src_info in import_result.sources:
            source_id = src_info["id"]
            existing_cookie = await db.scalar(
                select(Cookie).where(Cookie.source == source_id)
            )
            if existing_cookie is None:
                db.add(Cookie(
                    id=str(uuid4()),
                    source=source_id,
                    cookie_data=encrypt_cookie(payload.cookie.strip()),
                ))
        await db.commit()

    tasks: list[dict[str, Any]] = []
    if payload.discover:
        for src_info in import_result.sources:
            task = CrawlTask(
                id=str(uuid4()),
                source=src_info["id"],
                mode="discover_all",
                max_pages=payload.max_discover_pages,
                status="pending",
            )
            db.add(task)
            await db.flush()
            enqueue_crawl_all(task.source, task.max_pages, task.id)
            tasks.append({
                "id": task.id,
                "task_id": task.id,
                "source_id": task.source,
                "source_name": src_info.get("name", task.source),
                "status": task.status,
                "max_pages": task.max_pages,
                "mode": task.mode,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "error": task.error,
                "result": task.result,
                "progress": task.progress,
                "created_at": task.created_at,
            })
        await db.commit()

    return YueduImportTaskResult(tasks=tasks)


@router.post("/import-and-sync", response_model=YueduImportSyncResult, dependencies=[Depends(require_admin)])
async def import_and_sync_all(payload: YueduImportSyncRequest, db: AsyncSession = Depends(get_db)):
    """Import yuedu sources and immediately sync all discovered books.

    This is the primary one-click workflow:
    1. Import all book sources from the URL/JSON
    2. If cookie provided, save it for each source
    3. For each source, sync bookshelf (needs cookie)
    4. For each source, discover books from explore/category pages
    5. Return comprehensive results
    """
    from uuid import uuid4
    from app.models import Cookie
    from app.services.cookie_crypto import encrypt_cookie
    from app.services.sync import SyncService

    # Step 1: Import sources
    import_result = await import_yuedu_sources(
        YueduImportRequest(
            url=payload.url,
            json_text=payload.json_text,
            is_r18=payload.is_r18,
        ),
        db,
    )

    result = YueduImportSyncResult(
        sources_total=import_result.total,
        sources_imported=import_result.imported,
        sources_skipped=import_result.skipped,
        books_synced=0,
        chapters_downloaded=0,
        books_discovered=0,
        errors=[],
        details=[],
    )

    # Step 2: Save cookie if provided
    if payload.cookie and payload.cookie.strip():
        for src_info in import_result.sources:
            source_id = src_info["id"]
            existing_cookie = await db.scalar(
                select(Cookie).where(Cookie.source == source_id)
            )
            if existing_cookie is None:
                cookie_obj = Cookie(
                    id=str(uuid4()),
                    source=source_id,
                    cookie_data=encrypt_cookie(payload.cookie.strip()),
                )
                db.add(cookie_obj)
        await db.commit()

    # Step 3 & 4: Sync bookshelf + discover for each imported source
    sync_service = SyncService(db)
    for src_info in import_result.sources:
        source_id = src_info["id"]
        source_name = src_info.get("name", source_id)
        detail = {"source_id": source_id, "name": source_name, "sync": {}, "discover": {}}

        # Sync bookshelf
        try:
            # Check if there's a cookie for this source
            cookie_record = await db.scalar(
                select(Cookie).where(Cookie.source == source_id)
            )
            if cookie_record:
                shelf_result = await sync_service.sync_bookshelf(source_id)
                synced = sum(
                    r.get("created_chapters", 0)
                    for r in shelf_result.get("results", [])
                    if isinstance(r, dict)
                )
                detail["sync"] = {
                    "books_found": shelf_result.get("total", 0),
                    "chapters_downloaded": synced,
                }
                result.books_synced += shelf_result.get("total", 0)
                result.chapters_downloaded += synced
            else:
                detail["sync"] = {"skipped": "no cookie"}
        except Exception as exc:
            await db.rollback()
            detail["sync"] = {"error": str(exc)[:200]}
            result.errors.append({"source": source_id, "stage": "sync", "error": str(exc)[:200]})

        # Discover books from explore/category pages
        if payload.discover:
            try:
                discover_result = await sync_service.discover_and_sync_all(
                    source_id,
                    max_pages=payload.max_discover_pages,
                )
                result.books_discovered += discover_result["books_found"]
                result.chapters_downloaded += discover_result["chapters_created"]
                detail["discover"] = {
                    "pages_checked": discover_result["pages_checked"],
                    "books_found": discover_result["books_found"],
                    "books_synced": discover_result["books_synced"],
                    "books_failed": discover_result["books_failed"],
                    "chapters_created": discover_result["chapters_created"],
                }
            except Exception as exc:
                await db.rollback()
                detail["discover"] = {"error": str(exc)[:200]}

        result.details.append(detail)

    return result


@router.post("/preview", dependencies=[Depends(require_admin)])
async def preview_yuedu_sources(payload: YueduImportRequest):
    """Preview what sources a URL or JSON text would import without saving."""
    sources_json: list[dict[str, Any]] = []

    if payload.json_text:
        try:
            parsed = json.loads(payload.json_text)
            sources_json = _extract_sources(parsed)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    elif payload.url:
        try:
            resp = await _fetch_response(payload.url)
            parsed = resp.json()
            sources_json = _extract_sources(parsed)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {e}")
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"URL returned invalid JSON: {e}")

    else:
        raise HTTPException(status_code=400, detail="Either url or json_text is required")

    preview = []
    for src in sources_json:
        preview.append({
            "name": src.get("bookSourceName", "Unknown"),
            "url": src.get("bookSourceUrl", ""),
            "group": src.get("bookSourceGroup", ""),
            "is_r18": payload.is_r18,
            "type": _source_type_name(src.get("bookSourceType", 0)),
        })

    return {"count": len(preview), "sources": preview}


def _extract_sources(parsed: Any) -> list[dict[str, Any]]:
    """Extract source list from various JSON shapes."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        if "value" in parsed and isinstance(parsed["value"], list):
            return parsed["value"]
        if "bookSourceName" in parsed:
            return [parsed]
        for v in parsed.values():
            if isinstance(v, list):
                return v
    return []


def _make_source_id(name: str, url: str) -> str:
    """Generate a unique source ID from name and URL."""
    raw = f"{name}_{url}"
    return "yuedu_" + hashlib.md5(raw.encode()).hexdigest()[:12]


def _source_type_name(t: int) -> str:
    if t == 0:
        return "text"
    elif t == 1:
        return "audio"
    elif t == 2:
        return "image"
    return "unknown"
