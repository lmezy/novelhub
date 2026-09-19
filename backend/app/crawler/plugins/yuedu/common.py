"""Shared primitives for the YueDu (Legado) plugin package.

The plugin was one 6,000-line module with a 142-method class.  Splitting it into
mixins means every mixin needs the same handful of externals, and -- importantly
-- the same ``logger`` *object*.  ``logging.getLogger`` is keyed by name, so the
name below is deliberately the package's historical name: the crawler logs that
the runbooks and ``docs/codex-handoff.md`` tell operators to grep keep the exact
``app.crawler.plugins.yuedu`` prefix they always had.

This module must stay a leaf: it imports nothing from the package, so any mixin
can depend on it without creating an import cycle.
"""

import logging

__all__ = ["logger"]

# NOTE: keep this literal in sync with the package path.  Using ``__name__``
# here would yield ``app.crawler.plugins.yuedu.common`` and silently re-label
# every existing log line.
LOGGER_NAME = "app.crawler.plugins.yuedu"

logger = logging.getLogger(LOGGER_NAME)
