"""Crawler plugin registry.

Plugins are lazily instantiated. The yuedu plugin is special: each instance
must be configured with a YueDu book source JSON before use.

get_plugin() accepts both plugin names (e.g. "yuedu") and source IDs
(e.g. "yuedu_31a56dc1e2a9"). When given a source ID, it looks up the
Source record via a synchronous DB session to determine the plugin_name
and config.
"""

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
    record via a synchronous DB session to determine the plugin_name and config.
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
    """Look up a Source record via a synchronous DB session.

    Uses a sync session to avoid asyncpg event-loop conflicts when
    called from within an active async database session.
    """
    from app.core.config import settings
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.models import Source

    sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            source = db.get(Source, source_id)
            if source is None:
                raise ValueError(f"Source not found: {source_id}")
            config = source.config if source.plugin_name == "yuedu" else None
            return _instantiate(source.plugin_name, config)
    finally:
        engine.dispose()