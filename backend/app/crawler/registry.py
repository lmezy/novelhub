"""Crawler plugin registry.

Plugins are lazily instantiated. The yuedu plugin is special: each instance
must be configured with a YueDu book source JSON before use.
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
    YueduPlugin.name: _create_yuedu,  # factory: call with no args to get a new instance
}


def get_plugin(name: str, config: dict[str, Any] | None = None) -> NovelSourcePlugin:
    """Get a plugin instance by name.

    For the 'yuedu' plugin, the returned instance must be configured
    with plugin.configure(config) before use.
    """
    entry = plugins.get(name)
    if entry is None:
        available = ", ".join(sorted(plugins))
        raise ValueError(f"Unknown crawler plugin '{name}'. Available: {available}")

    if callable(entry):
        plugin = entry()
    else:
        plugin = entry

    # Configure yuedu plugin with source config
    if name == "yuedu" and config and hasattr(plugin, "configure"):
        plugin.configure(config)

    return plugin
