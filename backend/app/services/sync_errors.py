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
        message = str(exc).lower()
        if any(marker in message for marker in ("anti-bot", "captcha", "验证码", "rate-limit", "限流")):
            return True
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return status in {403, 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524}

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
        if core_transient.is_transient_transport_error(exc):
            # Covers the socket/stream class names *and* the "pop from an empty
            # deque" concurrency artefact: ten of these in a row used to abort
            # the whole task with a misleading "被反爬" message.
            return True
        message = str(exc).lower()
        if any(marker in message for marker in TRANSIENT_BOOK_MARKERS):
            return True
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return status in {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524}

    @staticmethod
    def _is_transient_chapter_error(exc: BaseException) -> bool:
        """Whether a chapter failed because the upstream/proxy was temporarily
        unavailable.

        Unlike an empty content rule, repeated 5xx/timeout failures are worth
        stopping the current book early for: continuing only sends dozens of
        doomed requests while the origin is down.
        """
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

