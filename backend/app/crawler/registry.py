from app.crawler.base import NovelSourcePlugin
from app.crawler.plugins.local_markdown import LocalMarkdownPlugin


plugins: dict[str, NovelSourcePlugin] = {
    LocalMarkdownPlugin.name: LocalMarkdownPlugin(),
}


def get_plugin(name: str) -> NovelSourcePlugin:
    try:
        return plugins[name]
    except KeyError as exc:
        available = ", ".join(sorted(plugins))
        raise ValueError(f"Unknown crawler plugin '{name}'. Available: {available}") from exc
