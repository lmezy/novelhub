"""YueDu book source import API.

Endpoints:
- POST /api/yuedu/import   Import yuedu sources from URL or raw JSON
- GET  /api/yuedu/preview  Preview what sources a URL would import
"""

import json
import hashlib
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import get_db
from app.models import Source
from app.services.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/yuedu", tags=["yuedu"])


class YueduImportRequest(BaseModel):
    url: str | None = None
    json_text: str | None = None


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
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(payload.url)
                resp.raise_for_status()
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
            config=src,
        )
        db.add(source)
        imported += 1
        results.append({"id": source_id, "name": name, "status": "imported", "bookshelf_url": None})

    await db.commit()

    # Auto-detect bookshelf URLs for imported sources
    for src_info in results:
        if src_info["status"] != "imported":
            continue
        try:
            source = await db.get(Source, src_info["id"])
            if source and source.config:
                from app.crawler.plugins.yuedu import YueduPlugin
                plugin = YueduPlugin(source.config)
                if source.config.get("bookshelf_url"):
                    src_info["bookshelf_url"] = source.config["bookshelf_url"]
                    continue
                detected = await plugin.detect_bookshelf_url()
                if detected:
                    source.config["bookshelf_url"] = detected
                    flag_modified(source, "config")
                    src_info["bookshelf_url"] = detected
                    await db.commit()
                    logger.info(f"Auto-detected bookshelf URL for {source.name}: {detected}")
        except Exception as e:
            logger.warning(f"Bookshelf auto-detect failed for {src_info['id']}: {e}")

    return YueduImportResult(
        total=total,
        imported=imported,
        skipped=skipped,
        sources=results,
    )


class YueduImportSyncRequest(BaseModel):
    url: str | None = None
    json_text: str | None = None
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
    from app.crawler.registry import get_plugin

    # Step 1: Import sources
    import_result = await import_yuedu_sources(
        YueduImportRequest(url=payload.url, json_text=payload.json_text),
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
            if src_info["status"] != "imported":
                continue
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
            detail["sync"] = {"error": str(exc)[:200]}
            result.errors.append({"source": source_id, "stage": "sync", "error": str(exc)[:200]})

        # Discover books from explore/category pages
        if payload.discover:
            try:
                source = await db.get(Source, source_id)
                if source:
                    config = source.config if source.plugin_name == "yuedu" else None
                    plugin = get_plugin(source.plugin_name, config=config)
                if source and hasattr(plugin, "discover_books"):
                    for page in range(1, payload.max_discover_pages + 1):
                        shelf_books = await plugin.discover_books(page=page)
                        for sb in shelf_books:
                            try:
                                await sync_service.sync_book(source_id, sb.url)
                                result.books_discovered += 1
                                result.chapters_downloaded += 1
                            except Exception:
                                pass
                        if len(shelf_books) == 0:
                            break
                detail["discover"] = {"pages_checked": payload.max_discover_pages}
            except Exception as exc:
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
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(payload.url)
                resp.raise_for_status()
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
