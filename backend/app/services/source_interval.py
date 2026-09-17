"""Per-source upstream request interval ("拉取间隔").

Some sites answer with a captcha or a 403 as soon as they see a second request
within their advertised limit (搬山人: one request per minute).  The book source
JSON often ships a much faster ``concurrentRate`` than the site actually
tolerates, and the rule file is not the right place to fix that: the interval
belongs to the *site*, so it is stored on the source row and editable from the
admin UI.

Semantics of ``sources.sync_interval_seconds``:

* ``None`` -- not configured; the source's own ``concurrentRate`` decides, then
  ``CRAWL_DELAY_MS`` (the pre-existing behaviour).
* ``0``    -- explicitly unthrottled for this source.
* ``N > 0``-- at most one upstream request per ``N`` seconds.
"""

import inspect
from typing import Any

from loguru import logger

__all__ = [
    "MAX_SYNC_INTERVAL_SECONDS",
    "apply_source_interval",
    "clamp_sync_interval",
    "source_sync_interval",
]

#: One hour.  Anything slower than this is not a request interval any more,
#: it is "this source should not be synced"; the auto-sync interval covers that.
MAX_SYNC_INTERVAL_SECONDS = 3600


def clamp_sync_interval(value: Any) -> int | None:
    """Normalise a configured interval; ``None`` when it is not configured."""
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(seconds, MAX_SYNC_INTERVAL_SECONDS))


def source_sync_interval(source: Any) -> int | None:
    """The interval configured on a ``Source`` row (``None`` when unset)."""
    return clamp_sync_interval(getattr(source, "sync_interval_seconds", None))


def apply_source_interval(plugin: Any, source: Any) -> int | None:
    """Hand a source's configured interval to a plugin that supports one.

    Only :class:`~app.crawler.plugins.yuedu.YueduPlugin` implements the setter
    (and the registry builds a fresh instance per source).  The other plugins
    are process-wide singletons, so deliberately *not* implementing
    ``set_request_interval_seconds`` keeps one source's throttle from leaking
    into the next source's sync.

    The setter's contract is synchronous: this helper is also called from
    non-async paths (``SyncService._source_plugin``).  A plugin that made it
    ``async`` would silently drop the throttle, so say so instead of leaking a
    "coroutine was never awaited" warning.
    """
    seconds = source_sync_interval(source)
    setter = getattr(plugin, "set_request_interval_seconds", None)
    if not callable(setter):
        return seconds
    result = setter(seconds)
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        logger.warning(
            "{}set_request_interval_seconds must be synchronous; "
            "the configured interval was not applied",
            f"{type(plugin).__name__}.",
        )
    return seconds
