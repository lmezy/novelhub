"""Fan out one keyword across every book source the caller may search.

Single-source search (``GET /sources/{id}/search``) answers with one page of one
site.  That is the wrong shape for the way book sources are actually used: a
reader who wants a novel has to guess which of their sources carries it, run the
search again per source, and wait for each one in turn.  Legado searches all
sources at once and renders each site's results as they arrive; this module is
that fan-out.

Design constraints that come from the transport layer:

* Every source keeps its **own** ``concurrentRate`` limiter
  (``transport._sleep_rate_limit``), so running N sources at once does not raise
  the request rate any single site sees.  ``SEARCH_FANOUT_CONCURRENCY`` therefore
  only bounds sockets and memory, not politeness.
* Sources are third-party hosts that may hang, return a captcha, or answer in
  30 s.  One dead host must never hold the whole request, so each source runs
  under its own timeout and the others stream out regardless.

The generator yields events instead of returning a list so the API can send them
with Server-Sent Events and the UI can render partial results immediately.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable

from loguru import logger
from sqlalchemy import select

from app.core.config import settings
from app.crawler.registry import get_plugin
from app.models import Book, Source, User
from app.services.source_interval import apply_source_interval
from app.services.visibility import can_view_all_ages, can_view_r18

__all__ = [
    "FanoutSource",
    "can_search_source",
    "fanout_concurrency",
    "fanout_max_sources",
    "fanout_results_per_source",
    "fanout_search",
    "fanout_source_timeout",
    "search_async",
    "searchable_plugin",
    "visible_sources",
]


@dataclass(frozen=True)
class FanoutSource:
    """One searchable source plus the plugin instance that speaks to it."""

    id: str
    name: str
    plugin: Any


def _int_setting(name: str, default: int, minimum: int) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def fanout_concurrency() -> int:
    """How many sources may be in flight at the same time (at least 1)."""
    return _int_setting("SEARCH_FANOUT_CONCURRENCY", 8, 1)


def fanout_source_timeout() -> float:
    """Seconds a single source may take before it is reported as timed out."""
    try:
        value = float(getattr(settings, "SEARCH_FANOUT_SOURCE_TIMEOUT_SECONDS", 20.0))
    except (TypeError, ValueError):
        value = 20.0
    return value if value > 0 else 20.0


def fanout_max_sources() -> int:
    """Ceiling on how many sources one request may query (0 = no ceiling)."""
    return _int_setting("SEARCH_FANOUT_MAX_SOURCES", 0, 0)


def fanout_results_per_source() -> int:
    """How many results each source may contribute (at least 1)."""
    return _int_setting("SEARCH_FANOUT_RESULTS_PER_SOURCE", 20, 1)


def can_search_source(user: User, source: Source) -> bool:
    """Whether ``user`` may search this source.

    Mirrors the single-source endpoint's gate exactly: an admin sees everything,
    and the R18 / all-ages preference decides which half of the catalogue the
    source belongs to.  Kept here (rather than in the route module) so the
    one-source and fan-out paths answer the same question from one place.
    """
    if user.role in ("admin", "super_admin"):
        return True
    if source.owner_id is not None and source.owner_id != user.id:
        return False
    if source.is_r18 and not can_view_r18(user):
        return False
    if not source.is_r18 and not can_view_all_ages(user):
        return False
    return True


def searchable_plugin(source: Source) -> Any | None:
    """Build a plugin for ``source``, or ``None`` when it cannot search.

    The registry hands out a *fresh* instance for every call, so a fan-out over
    many sources never shares mutable plugin state between them.  A source whose
    plugin has no ``search_books`` (the local filesystem plugin, for instance) is
    skipped rather than reported as a failure.
    """
    config = source.config if source.plugin_name == "yuedu" else None
    try:
        plugin = get_plugin(source.plugin_name, config=config)
    except Exception as exc:  # noqa: BLE001 - any registry failure is per-source
        logger.warning("Cannot build plugin for source {}: {}", source.id, exc)
        return None
    if not callable(getattr(plugin, "search_books", None)):
        return None
    apply_source_interval(plugin, source)
    return plugin


async def visible_sources(db, user: User, source_ids: Iterable[str] | None = None):
    """Every enabled source ``user`` may search, ordered by name.

    ``source_ids`` narrows the fan-out to a hand-picked set (the UI lets a user
    choose), but the visibility rules still apply: naming a source cannot make an
    invisible one reachable.
    """
    query = select(Source).where(Source.enabled == True)  # noqa: E712
    ids = [str(value) for value in (source_ids or []) if str(value).strip()]
    if ids:
        query = query.where(Source.id.in_(ids))
    sources = list(await db.scalars(query.order_by(Source.name.asc())))
    allowed = [source for source in sources if can_search_source(user, source)]
    cap = fanout_max_sources()
    return allowed[:cap] if cap > 0 else allowed


async def _library_index(db, source_id: str, items: list[dict]) -> dict[str, str]:
    """Map a page of remote results to the ids of the books already imported.

    Uses the same two candidate keys as the single-source endpoint (the cleaned
    book URL and its last path segment) so "已入库" means the same thing in both
    views.
    """
    candidates: set[str] = set()
    for item in items:
        url = _clean_url(item)
        if url:
            candidates.add(url)
        last = url.rstrip("/").split("/")[-1] if "/" in url else url
        if last:
            candidates.add(last)
    if not candidates:
        return {}
    rows = await db.execute(
        select(Book.id, Book.source_book_id).where(
            Book.source_id == source_id,
            Book.source_book_id.in_(list(candidates)),
        )
    )
    return {
        source_book_id: book_id
        for book_id, source_book_id in rows
        if source_book_id
    }


def _clean_url(item: dict) -> str:
    """The book URL with any Legado ``,{...}`` option suffix removed.

    Sources append ``,{"method":"POST",...}`` (webView/POST) to book links; the
    library stores the clean form, so the suffix has to go before either can be
    compared.
    """
    from app.crawler.plugins.yuedu import YueduPlugin

    raw = str(item.get("bookUrl") or item.get("url") or "").strip()
    return YueduPlugin._strip_url_options_suffix(raw)


def serialize_item(item: dict, library_map: dict[str, str]) -> dict:
    """Shape one rule-parsed result the way the search UI expects it."""
    url = str(item.get("bookUrl") or item.get("url") or "").strip()
    clean = _clean_url(item)
    last = clean.rstrip("/").split("/")[-1] if "/" in clean else clean
    book_id = library_map.get(clean) or library_map.get(last)
    return {
        "name": str(item.get("name") or "").strip() or "Unknown",
        "author": str(item.get("author") or "").strip() or "Unknown",
        "url": url,
        "cover_url": item.get("coverUrl"),
        "intro": item.get("intro"),
        "kind": item.get("kind"),
        "latest_chapter": item.get("lastChapter"),
        "word_count": item.get("wordCount"),
        "in_library": book_id is not None,
        "book_id": book_id,
    }


async def search_async(
    db,
    user: User,
    keyword: str,
    *,
    source_ids: Iterable[str] | None = None,
    limit_per_source: int | None = None,
) -> AsyncIterator[dict]:
    """Search every visible source at once, yielding one event per source.

    Events:

    ``{"type": "start", "sources": N}``
        Emitted once, before any upstream request, so the UI can render the
        progress list immediately.
    ``{"type": "source", "source_id", "source_name", "count", "elapsed_ms",
       "results": [...], "error": null | str}``
        Emitted as each source settles -- succeeded, failed or timed out.
    ``{"type": "done", "searched": N, "failed": N}``
        Emitted once, after the last source.

    All database access is serialized behind a lock: every worker shares the
    request's single ``AsyncSession``, and asyncpg refuses two operations on one
    connection at the same time.
    """
    query = (keyword or "").strip()
    if not query:
        yield {"type": "done", "searched": 0, "failed": 0}
        return

    sources = await visible_sources(db, user, source_ids)
    per_source = limit_per_source or fanout_results_per_source()
    timeout = fanout_source_timeout()

    prepared: list[FanoutSource] = []
    for source in sources:
        plugin = searchable_plugin(source)
        if plugin is not None:
            prepared.append(FanoutSource(id=source.id, name=source.name, plugin=plugin))

    yield {"type": "start", "sources": len(prepared)}
    if not prepared:
        yield {"type": "done", "searched": 0, "failed": 0}
        return

    concurrency = min(fanout_concurrency(), len(prepared))
    semaphore = asyncio.Semaphore(concurrency)
    events: asyncio.Queue[dict] = asyncio.Queue()
    queue: asyncio.Queue[FanoutSource | None] = asyncio.Queue()
    for entry in prepared:
        queue.put_nowait(entry)
    for _ in range(concurrency):
        queue.put_nowait(None)

    db_lock = asyncio.Lock()

    async def run_one(entry: FanoutSource) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()

        def failure(message: str) -> dict:
            return {
                "type": "source",
                "source_id": entry.id,
                "source_name": entry.name,
                "count": 0,
                "elapsed_ms": int((loop.time() - started) * 1000),
                "results": [],
                "error": message[:200],
            }

        try:
            async with semaphore:
                items = list(
                    await asyncio.wait_for(
                        entry.plugin.search_books(query, page=1, limit=per_source),
                        timeout=timeout,
                    )
                    or []
                )
        except asyncio.TimeoutError:
            await events.put(failure(f"timeout after {timeout:.0f}s"))
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one site's failure is just data
            await events.put(failure(str(exc) or type(exc).__name__))
            return
        items = items[:per_source]

        # Outside the semaphore: this is our own database, and holding an
        # upstream slot while querying it would serialize the fan-out behind
        # Postgres instead of behind the sites.
        try:
            async with db_lock:
                library = await _library_index(db, entry.id, items)
        except Exception as exc:  # noqa: BLE001 - the search itself succeeded
            logger.warning("Library lookup failed for {}: {}", entry.id, exc)
            library = {}
        await events.put({
            "type": "source",
            "source_id": entry.id,
            "source_name": entry.name,
            "count": len(items),
            "elapsed_ms": int((loop.time() - started) * 1000),
            "results": [serialize_item(item, library) for item in items],
            "error": None,
        })

    async def worker() -> None:
        while True:
            entry = await queue.get()
            if entry is None:
                return
            try:
                await run_one(entry)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a worker must never die
                logger.opt(exception=exc).warning(
                    "Fan-out search worker failed for {}", entry.id
                )
                await events.put({
                    "type": "source",
                    "source_id": entry.id,
                    "source_name": entry.name,
                    "count": 0,
                    "elapsed_ms": 0,
                    "results": [],
                    "error": str(exc)[:200] or type(exc).__name__,
                })

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    searched = 0
    failed = 0
    try:
        for _ in range(len(prepared)):
            event = await events.get()
            if event.get("error"):
                failed += 1
            else:
                searched += 1
            yield event
    finally:
        for task in workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    yield {"type": "done", "searched": searched, "failed": failed}


async def fanout_search(
    db,
    user: User,
    keyword: str,
    *,
    source_ids: Iterable[str] | None = None,
    limit_per_source: int | None = None,
) -> dict:
    """Collect :func:`search_async` into a single response.

    Used by callers that cannot stream (tests, scripts).  The API streams; this
    exists so both shapes share one implementation.
    """
    per_source: list[dict] = []
    failures: list[dict] = []
    total = 0
    async for event in search_async(
        db, user, keyword, source_ids=source_ids, limit_per_source=limit_per_source
    ):
        if event.get("type") == "start":
            total = int(event.get("sources") or 0)
        elif event.get("type") == "source":
            entry = {
                "source_id": event["source_id"],
                "source_name": event["source_name"],
                "count": event["count"],
                "elapsed_ms": event["elapsed_ms"],
                "results": event["results"],
            }
            if event.get("error"):
                failures.append(dict(entry, error=event["error"]))
            else:
                per_source.append(entry)
    return {
        "query": keyword,
        "sources": total,
        "results": per_source,
        "failures": failures,
    }
