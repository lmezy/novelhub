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
