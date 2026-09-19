"""Retryable-failure taxonomy for the YueDu plugin.

This module used to own its own list of transient exception class names, which
drifted from the task-level list in ``services/sync.py`` -- ``EndOfStream`` and
``WouldBlock`` were retryable here but permanent there, so such a failure could
abort a whole sync task.

The taxonomy now lives in exactly one place, :mod:`app.core.transient`.  This
module stays as the plugin's import path (``transport``, ``images`` and ``book``
import from here, and tests do ``from app.crawler.plugins.yuedu import
is_transient_transport_error``), re-exporting that single source of truth rather
than restating it.
"""

from app.core.transient import (  # noqa: F401
    TRANSIENT_EXCEPTION_NAMES as TRANSIENT_TRANSPORT_ERROR_NAMES,
    TRANSIENT_MESSAGE_MARKERS,
    exception_names,
    is_transient_transport_error,
)

__all__ = [
    "TRANSIENT_TRANSPORT_ERROR_NAMES",
    "TRANSIENT_MESSAGE_MARKERS",
    "exception_names",
    "is_transient_transport_error",
]
