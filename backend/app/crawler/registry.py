"""Crawler plugin registry.

Plugins are lazily instantiated. The yuedu plugin is special: each instance
must be configured with a YueDu book source JSON before use.

get_plugin() accepts both plugin names (e.g. "yuedu") and source IDs
(e.g. "yuedu_31a56dc1e2a9"). When given a source ID, it looks up the
Source record to determine the plugin_name and config.
"""

import asyncio
from typing import Any

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
    """Get a plugin instance by plugin name or source ID.

    When given a source ID (not a known plugin name), looks up the Source
    record to determine the actual plugin_name and fetches its config.
    """
    if name in plugins:
        return _instantiate(name, config)
    try:
        return _get_plugin_by_source_id(name)
    except Exception:
        available = ", ".join(sorted(plugins))
        raise ValueError(f"Unknown crawler plugin or source '{name}'. Available plugins: {available}")


def _instantiate(plugin_name: str, config: dict[str, Any] | None = None) -> NovelSourcePlugin:
    entry = plugins[plugin_name]
    if callable(entry):
        plugin = entry()
    else:
        plugin = entry
    if plugin_name == "yuedu" and config and hasattr(plugin, "configure"):
        plugin.configure(config)
    return plugin


def _get_plugin_by_source_id(source_id: str) -> NovelSourcePlugin:
    """Look up a Source record and return the configured plugin."""
    from app.core.database import SessionLocal
    from app.models import Source
    from sqlalchemy import select

    async def _lookup() -> NovelSourcePlugin:
        async with SessionLocal() as db:
            source = await db.get(Source, source_id)
            if source is None:
                raise ValueError(f"Source not found: {source_id}")
            config = source.config if source.plugin_name == "yuedu" else None
            return _instantiate(source.plugin_name, config)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_lookup())
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(lambda: asyncio.run(_lookup()))
            return future.result()