"""Transient/permanent error classification for the sync service.

Split out of ``sync.py``: these pure predicates decide whether a failed fetch
is worth retrying, so they live apart from the orchestration code.
"""

from app.core import transient as core_transient


# Book-level transient markers (a book that failed for one of these is retried
# later; "empty content" counts here because a source can answer with an empty
# page while a site is degraded).
TRANSIENT_BOOK_MARKERS = (
    "5xx", "browser request failed", "timeout", "timed out",
    "connection", "connect error", "unknown error", "error code 5",
    "upstream server returned", "empty content",
    # ``_get``/``_post`` only give up after three attempts, and the 429/5xx
    # retry branch used to leave ``last_error`` empty, so this string is the
    # "the site kept answering 5xx" signature (风月文学網 sweeps).
    "request failed after retries",
)

# Chapter-level markers.  Deliberately narrower than the book set: an empty
# chapter must not be treated as a network hiccup, otherwise five broken
# chapters in a row would abort a book that is merely badly parsed.
TRANSIENT_CHAPTER_MARKERS = (
    "5xx", "upstream server returned", "browser request failed",
    "timeout", "timed out", "connection", "connect error", "network",
    "request failed after retries",
)



class SyncErrorMixin:
    @staticmethod
    def _is_permanent_chapter_error(exc: BaseException) -> bool:
        """Whether retrying cannot possibly help.

        A chapter the source deleted keeps answering with the same notice
        page, so retrying only adds three requests per chapter.
        """
        message = str(exc)
        return any(
            marker in message
            for marker in ("已被删除或禁用", "已被删除", "已下架", "不存在")
        )

    @staticmethod
    def _is_upstream_blocked(exc: BaseException) -> bool:
        """Recognize WAF/rate-limit responses even when HTTPX exposes only a
        status line (for example ``403 Forbidden``) rather than the HTML
        anti-bot marker returned by the source.

        Infrastructure failures (a full SQLAlchemy pool, a missing greenlet
        context) are deliberately *not* upstream blocks: they say nothing
        about the site, and classifying them here made pool exhaustion abort
        books and tasks with a misleading captcha/anti-crawl error.
        """
        if SyncErrorMixin._is_infrastructure_error(exc):
            return False
        status = core_transient.http_status(exc)
        if status is not None:
            return status == 403
        # Empty-directory diagnostics can mention a *possible* rate limit.
        # Such a suggestion is not evidence that the server refused access.
        if SyncErrorMixin._is_empty_book_error(exc):
            return False
        message = str(exc).lower()
        return any(marker in message for marker in ("anti-bot", "captcha", "验证码", "rate-limit", "限流"))

    #: Failures of our own stack, not of the site.  They carry a message rather
    #: than a recognisable class name, so they are matched on text -- the one
    #: message-based carve-out in this module.  Stricter matching (``queuepool``
    #: rather than ``pool``) keeps real site errors such as "pool is banned"
    #: out of this bucket.
    INFRASTRUCTURE_ERROR_MARKERS = (
        "queuepool limit",
        "queuepool",
        "connection timed out, timeout 30.00",
        "greenlet_spawn has not been called",
        "can't call await_only",
        "missinggreenlet",
        "this session's transaction has been rolled back",
    )

    @staticmethod
    def _is_infrastructure_error(exc: BaseException) -> bool:
        """Whether ``exc`` is our own stack failing, not the site or proxy.

        A ``QueuePool limit ... reached`` timeout, a ``MissingGreenlet`` from a
        rolled-back session, and friends mean "the crawler ran out of database
        connections", not "the site is blocking us".  Counting them toward the
        consecutive-failure abort hid pool exhaustion behind a captcha/anti-crawl
        message and -- worse -- aborted healthy tasks because of it.
        """
        seen: set[int] = set()
        cause: object | None = exc
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            try:
                message = f"{type(cause).__name__} {cause}".lower()
            except Exception:
                break
            if any(marker in message for marker in SyncErrorMixin.INFRASTRUCTURE_ERROR_MARKERS):
                return True
            cause = getattr(cause, "__cause__", None) or getattr(
                cause, "__context__", None
            )
        return False

    @staticmethod
    def _is_transient_book_fetch(exc: BaseException) -> bool:
        """Whether a failed book fetch is a transient error worth retrying
        (Cloudflare 5xx / upstream error, browser load timeout, or a dropped
        connection) rather than a deterministic rule/Cookie problem."""
        status = core_transient.http_status(exc)
        if status is not None:
            return core_transient.retryable_http_status(status)
        if core_transient.is_transient_transport_error(exc):
            # Covers the socket/stream class names *and* the "pop from an empty
            # deque" concurrency artefact: ten of these in a row used to abort
            # the whole task with a misleading "被反爬" message.
            return True
        message = str(exc).lower()
        return any(marker in message for marker in TRANSIENT_BOOK_MARKERS)

    @staticmethod
    def _is_transient_chapter_error(exc: BaseException) -> bool:
        """Whether a chapter failed because the upstream/proxy was temporarily
        unavailable.

        Unlike an empty content rule, repeated 5xx/timeout failures are worth
        stopping the current book early for: continuing only sends dozens of
        doomed requests while the origin is down.
        """
        status = core_transient.http_status(exc)
        if status is not None:
            return core_transient.retryable_http_status(status)
        if core_transient.is_transient_transport_error(exc):
            return True
        return any(marker in str(exc).lower()
                   for marker in TRANSIENT_CHAPTER_MARKERS)

    @staticmethod
    def _is_empty_book_error(exc: BaseException) -> bool:
        """Whether ``exc`` is the deterministic "book page parsed to nothing".

        ``sync_book`` raises ``ValueError("Book page returned no usable
        metadata/chapters: ...")`` when the page answered but the rules
        extracted no usable title/chapters -- a stale rule or a removed book,
        never a transient outage.  Retrying fetches the same page, and
        counting it toward ``SYNC_MAX_CONSECUTIVE_FAILURES`` aborted whole
        tasks (wn09: 30 synced / 32 failed) for a rule problem.
        """
        return "no usable metadata" in str(exc).lower()


# Failures that are worth retrying on their own: the proxy/upstream was
# momentarily unreachable, not the source rules or a captcha gate.  A short
# outage used to mark the whole task failed (and the UI then showed a
# misleading "书源未返回可同步的书籍").
_TRANSIENT_TASK_MARKERS = (
    "timeout",
    "timed out",
    "connecttimeout",
    "readtimeout",
    "pooltimeout",
    "connecterror",
    "readerror",
    "writeerror",
    "remoteprotocolerror",
    "proxyerror",
    "connection reset",
    "connection closed",
    "connection is closed",
    "connection was closed",
    "connection is already closed",
    "server closed the connection",
    "connection refused",
    "connection aborted",
    "temporarily unavailable",
    "temporary failure",
    "network is unreachable",
    "name or service not known",
    "getaddrinfo failed",
    "upstream server returned",
    "server returned a transient",
    "error code 5",
    "empty content",
    "网络",
    "暂时无法访问",
)

# These need a Cookie / a different exit node, so retrying immediately only
# hammers the site.
_NON_TRANSIENT_TASK_MARKERS = (
    "anti-bot",
    "captcha",
    "验证码",
    "人机验证",
    "身份验证",
    "反爬",
    "cookie",
    "legado js",
    "书源规则",
)


def is_transient_task_error(exc: BaseException) -> bool:
    """Whether a failed crawl task should be retried automatically.

    A dropped database connection surfaces two layers up: the asyncpg
    ``InterfaceError`` ("the underlying connection is closed") is the real cause,
    and SQLAlchemy wraps it in a ``PendingRollbackError`` whose own name and
    message only *embed* the original as text.  Reading just the wrapper missed
    exactly that case on 2026-10-13: a three-day task was marked ``failed``
    (its counters read "成功 1147 / 找到 1168") because the final progress write
    hit a connection the server had closed.  Walk the explicit cause chain
    (``__cause__`` / ``.orig``, deliberately not the implicit ``__context__``)
    so the actual failure is what gets classified.
    """

    def _nodes():
        seen: set[int] = set()
        node: BaseException | None = exc
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            yield node
            node = getattr(node, "orig", None) or node.__cause__

    for node in _nodes():
        status = core_transient.http_status(node)
        if status is not None:
            return core_transient.retryable_http_status(status)
        name = type(node).__name__.lower()
        message = str(node).lower()
        if any(marker in message for marker in _NON_TRANSIENT_TASK_MARKERS):
            return False
        if any(marker in message for marker in _TRANSIENT_TASK_MARKERS):
            return True
        if core_transient.is_transient_transport_error(node):
            return True
        # asyncpg ``InterfaceError`` / SQLAlchemy ``DBAPIError`` normally already
        # carry "connection is closed" in the message (matched above); this name
        # gate is the fallback for a future wording that still means "the
        # connection died", while excluding the *other* ``InterfaceError``
        # ("another operation is in progress"), which is not a dropped link.
        if name in {"interfaceerror", "dbapierror"} and (
            "connection" in message or "closed" in message
        ):
            return True
    return False
