"""The single taxonomy of retryable transport failures.

Why this module exists
----------------------
The crawler used to carry **two** independent transient-error lists: one in the
YueDu plugin (``plugins/yuedu/errors.py``, deciding whether a request or image is
worth re-dialing) and one in ``services/sync.py`` (deciding whether a failed
book/chapter is a network blip or a deterministic rule/Cookie problem).  They
drifted.  ``EndOfStream`` and ``WouldBlock`` were retryable in the plugin but
*permanent* at the task level, so such a failure counted toward "连续失败" and
could abort a whole task -- the exact misdiagnosis this project spent several
incidents removing.

Classification is deliberately by **class name**, not ``isinstance``
---------------------------------------------------------------
``isinstance`` against real classes would require importing httpx, anyio,
playwright and httpcore here, and would silently miss any exception type raised
by a library this module does not import -- which is the whole point: the
exceptions come from wherever the request stack happens to live.  Matching the
class names in the exception's MRO is library-agnostic and cheap, and it still
works when ``str(exc)`` is empty (httpx and asyncio build timeouts with no
arguments, which is how "Failed to sync book X: " with no cause used to appear).

``RequestError`` is excluded on purpose
---------------------------------------
``httpx.HTTPStatusError`` derives from ``httpx.RequestError``, so listing
``RequestError`` makes **every** HTTP status error look like a network blip --
including a 404.  Callers must decide those on the status code (see
``SyncService._is_transient_book_fetch``), not on the exception's ancestry.
"""

from __future__ import annotations

__all__ = [
    "TRANSIENT_EXCEPTION_NAMES",
    "TRANSIENT_MESSAGE_MARKERS",
    "exception_names",
    "is_transient_transport_error",
]

#: Exception class names that mean "the network/proxy hiccuped, dial again",
#: even when the exception carries no message at all.
TRANSIENT_EXCEPTION_NAMES = frozenset({
    # builtins / asyncio / playwright
    "TimeoutError",           # asyncio.TimeoutError is the builtin on 3.11+
    "ConnectionError",
    # httpx timeouts and transport errors
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "TimeoutException",       # httpx timeout base class (NOT a parent of
                              # HTTPStatusError, so this one is safe to list)
    "ConnectError",
    "ReadError",
    "WriteError",
    "RemoteProtocolError",
    "TransportError",         # httpx base class for the transport errors above
    # httpcore / aiohttp connection failures seen through the request stack
    "NetworkError",
    "ClientConnectionError",
    "ServerDisconnectedError",
    # anyio stream errors.  httpx runs on anyio, and when a pooled client or its
    # socket is torn down (proxy restart, mihomo reload) the in-flight requests
    # surface these instead of an httpx exception; they stringify to "" so only
    # the class name reveals what happened (crawler container, 2026-09-14).
    "ClosedResourceError",
    "BrokenResourceError",
    "BusyResourceError",
    "IncompleteReadError",
    # asyncio streams can end mid-read during a proxy restart.  These two were
    # recognised by the plugin's retry loop but missing from the task-level
    # list, so a task could abort on a failure it should have retried.
    "EndOfStream",
    "WouldBlock",
})

#: Concurrency artefacts raised from inside the transport/browser stack while a
#: shared client is being replaced.  They carry a message rather than a
#: recognisable class name, so they are the one message-based exception here.
TRANSIENT_MESSAGE_MARKERS = (
    "pop from an empty deque",
)


def exception_names(exc: BaseException) -> set[str]:
    """All class names in an exception's MRO (works without importing HTTPX)."""
    return {cls.__name__ for cls in type(exc).__mro__}


def is_transient_transport_error(exc: BaseException) -> bool:
    """Whether ``exc`` is a socket/stream failure that is worth retrying.

    This is the single answer used by both the plugin's request/image retry
    loops and the sync service's book/chapter classification.
    """
    # A subclass of a builtin timeout/connection error whose own name is not in
    # the set above (Playwright and friends raise their own subclasses).
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if exception_names(exc) & TRANSIENT_EXCEPTION_NAMES:
        return True
    lowered = str(exc).lower()
    return any(marker in lowered for marker in TRANSIENT_MESSAGE_MARKERS)
