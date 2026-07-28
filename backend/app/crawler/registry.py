from app.crawler.base import NovelSourcePlugin
from app.crawler.plugins.local_markdown import LocalMarkdownPlugin
from app.crawler.plugins.alicesw import AliceSWPlugin
from app.crawler.plugins.qidian import QidianPlugin
from app.crawler.plugins.fanqie import FanqiePlugin


plugins: dict[str, NovelSourcePlugin] = {
    LocalMarkdownPlugin.name: LocalMarkdownPlugin(),
    AliceSWPlugin.name: AliceSWPlugin(),
    QidianPlugin.name: QidianPlugin(),
    FanqiePlugin.name: FanqiePlugin(),
}


def get_plugin(name: str) -> NovelSourcePlugin:
    try:
        return plugins[name]
    except KeyError as exc:
        available = ", ".join(sorted(plugins))
        raise ValueError(f"Unknown crawler plugin '{name}'. Available: {available}") from exc
