"""Crawler plugin registry.

Plugins are lazily instantiated. The yuedu plugin is special: each instance
must be configured with a YueDu book source JSON before use.

get_plugin() accepts both plugin names (e.g. "yuedu") and source IDs.
"""

import asyncio
import concurrent.futures
from typing import Any

from loguru import logger

from app.crawler.base import NovelSourcePlugin
from app.crawler.plugins.local_markdown import LocalMarkdownPlugin
from app.crawler.plugins.alicesw import AliceSWPlugin
from app.crawler.plugins.qidian import QidianPlugin
from app.crawler.plugins.fanqie import FanqiePlugin
from app.crawler.plugins.yuedu import YueduPlugin


def _create_yuedu() -> YueduPlugin:
    return YueduPlugin()


plugins: dict[str, Any] = {
    LocalMarkdownPlugin.name: LocalMarkdownPlugin(),
    AliceSWPlugin.name: AliceSWPlugin(),
    QidianPlugin.name: QidianPlugin(),
    FanqiePlugin.name: FanqiePlugin(),
    YueduPlugin.name: _create_yuedu,
}


def get_plugin(name: str, config: dict[str, Any] | None = None) -> NovelSourcePlugin:
    """Get a plugin instance by plugin name or source ID."""
    if name in plugins:
        return _instantiate(name, config)
    try:
        # Try asyncio.run first (works outside event loops, e.g. Celery tasks)
        return asyncio.run(_lookup_source_async(name))
    except RuntimeError:
        # Already inside an event loop (FastAPI handler) — use a thread
        # with its own event loop. The thread uses a SEPARATE async session
        # to avoid asyncpg connection conflicts.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_lookup_source_async(name))).result()
    except Exception as exc:
        logger.opt(exception=exc).warning("get_plugin failed for '{}'", name)
        available = ", ".join(sorted(plugins))
        raise ValueError(f"Unknown plugin or source '{name}'. Available: {available}")


def _instantiate(plugin_name: str, config: dict[str, Any] | None = None) -> NovelSourcePlugin:
    entry = plugins[plugin_name]
    if callable(entry):
        plugin = entry()
    else:
        plugin = entry
    if plugin_name == "yuedu" and config and hasattr(plugin, "configure"):
        plugin.configure(config)
    return plugin


async def _lookup_source_async(source_id: str) -> NovelSourcePlugin:
    """Look up a Source record using its own async database session."""
    from app.core.database import SessionLocal
    from app.models import Source
    from sqlalchemy import select

    async with SessionLocal() as db:
        source = await db.get(Source, source_id)
        if source is None:
            raise ValueError(f"Source not found: {source_id}")
        config = source.config if source.plugin_name == "yuedu" else None
        logger.debug("Resolved source {} -> plugin {}", source_id, source.plugin_name)
        return _instantiate(source.plugin_name, config)