"""The retryable-failure taxonomy must have exactly one source of truth.

Two independent lists used to exist -- the YueDu plugin's (deciding whether to
re-dial a request or image) and ``services/sync.py``'s (deciding whether a failed
book/chapter is a network blip or a deterministic rule/Cookie problem).  They
drifted: ``EndOfStream`` and ``WouldBlock`` were retryable in the plugin but
*permanent* at the task level, so such a failure counted toward "连续失败" and
could abort an entire sync task -- the same misdiagnosis that cost several
incidents (docs/codex-handoff.md sections 13/14/15).

These tests pin the single classifier and the two deliberate exclusions, so a
future "let's just add the httpx base class" edit cannot quietly reintroduce the
over-classification.
"""

import httpx
import pytest
from anyio import ClosedResourceError, EndOfStream, WouldBlock

from app.core import transient as core_transient
from app.crawler.plugins import yuedu as yuedu_package
from app.crawler.plugins.yuedu import errors as plugin_errors
from app.services import sync as sync_module
from app.services.sync import SyncService

# Every exception the request stack can realistically hand us, plus two that
# must never be treated as a network blip.
REPRESENTATIVE = [
    httpx.ConnectTimeout(""),
    httpx.ReadTimeout(""),
    httpx.WriteTimeout(""),
    httpx.PoolTimeout(""),
    httpx.ConnectError(""),
    httpx.ReadError(""),
    httpx.WriteError(""),
    httpx.RemoteProtocolError(""),
    httpx.TransportError(""),
    ClosedResourceError(""),
    EndOfStream(""),
    WouldBlock(""),
    TimeoutError(""),
    ConnectionError(""),
    IndexError("pop from an empty deque"),
    IndexError("list index out of range"),
    RuntimeError("no content"),
    httpx.HTTPStatusError(
        "404", request=httpx.Request("GET", "https://example.com/a"),
        response=httpx.Response(404),
    ),
]


def test_the_plugin_and_the_service_share_one_taxonomy_object():
    """Not "equal" -- the same object, so they cannot drift apart again."""
    assert plugin_errors.TRANSIENT_TRANSPORT_ERROR_NAMES \
        is core_transient.TRANSIENT_EXCEPTION_NAMES


def test_the_service_module_keeps_no_second_copy_of_the_taxonomy():
    """The service must classify *through* ``core_transient``, not beside it.

    ``services/sync.py`` used to re-export ``TRANSIENT_EXCEPTION_NAMES`` (and a
    ``_exception_names`` alias) for its own call sites.  Both were unused, and
    any local copy is exactly how the two layers drifted in the first place --
    so their absence is the invariant now, not their presence.
    """
    for name in ("TRANSIENT_EXCEPTION_NAMES", "_exception_names"):
        assert not hasattr(sync_module, name), (
            f"services/sync.py reintroduced {name}; classify through "
            "app.core.transient instead"
        )


def test_the_service_classifies_through_the_shared_object():
    """A representative transient error reaches ``core_transient`` from here."""
    assert SyncService._is_transient_book_fetch(httpx.ReadTimeout("")) is True
    assert SyncService._is_transient_book_fetch(IndexError("list index out of range")) is False


def test_both_import_paths_expose_the_same_classifier():
    """``from app.crawler.plugins.yuedu import ...`` must keep working."""
    assert yuedu_package.is_transient_transport_error \
        is core_transient.is_transient_transport_error
    assert plugin_errors.is_transient_transport_error \
        is core_transient.is_transient_transport_error


@pytest.mark.parametrize("exc", REPRESENTATIVE,
                         ids=lambda e: type(e).__name__ + ":" + repr(str(e))[:14])
def test_the_classifier_agrees_across_layers(exc):
    """The plugin verdict and the service verdict may never disagree.

    This is the invariant that the drift violated: a failure retryable in the
    request loop has to be retryable (not abort-worthy) at the task level.
    """
    plugin_says = core_transient.is_transient_transport_error(exc)
    service_says = bool(
        core_transient.exception_names(exc)
        & core_transient.TRANSIENT_EXCEPTION_NAMES
    ) or any(
        marker in str(exc).lower()
        for marker in core_transient.TRANSIENT_MESSAGE_MARKERS
    ) or isinstance(exc, (TimeoutError, ConnectionError))
    assert plugin_says == service_says, (
        "%s classified differently: plugin=%s service=%s"
        % (type(exc).__name__, plugin_says, service_says)
    )


@pytest.mark.parametrize("exc", [EndOfStream(""), WouldBlock("")])
def test_the_previously_drifted_names_are_transient_at_both_layers(exc):
    """These two were the actual drift.

    Retryable inside the plugin, permanent in ``services/sync.py`` -- so an
    asyncio stream ending mid-read during a proxy restart could be counted as a
    hard failure and abort the task.
    """
    assert core_transient.is_transient_transport_error(exc) is True
    assert SyncService._is_transient_book_fetch(exc) is True
    assert SyncService._is_transient_chapter_error(exc) is True


def test_request_error_is_excluded_on_purpose():
    """``httpx.HTTPStatusError`` derives from ``httpx.RequestError``.

    Listing ``RequestError`` therefore makes *every* HTTP status error look like
    a network blip -- a 404 included.  Status codes are judged on the status
    code, not on the exception's ancestry, so the base class must stay out.
    """
    assert "RequestError" not in core_transient.TRANSIENT_EXCEPTION_NAMES
    assert "HTTPError" not in core_transient.TRANSIENT_EXCEPTION_NAMES


def _status_error(code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        str(code), request=httpx.Request("GET", "https://example.com/a"),
        response=httpx.Response(code),
    )


def test_a_404_is_not_transient_but_a_503_is():
    """The bug the exclusion fixes: a 404 used to be retried as a network blip."""
    assert core_transient.is_transient_transport_error(_status_error(404)) is False
    assert SyncService._is_transient_book_fetch(_status_error(404)) is False

    # A 5xx still has to be retryable -- via the status code, which is where
    # that decision belongs.
    assert SyncService._is_transient_book_fetch(_status_error(503)) is True
    assert SyncService._is_transient_book_fetch(_status_error(429)) is True


def test_empty_message_timeouts_still_stringify_to_a_cause():
    """``describe_error`` exists because these stringify to ""."""
    assert str(httpx.ReadTimeout("")) == ""
    assert sync_module.describe_error(httpx.ReadTimeout("")) == "ReadTimeout"
    assert sync_module.describe_error(None) == "Unknown error"


def test_the_concurrency_artefact_is_still_matched_by_its_message():
    """``pop from an empty deque`` carries no useful class name."""
    assert core_transient.is_transient_transport_error(
        IndexError("pop from an empty deque")) is True
    # The other IndexError must not be swept in by a loose match.
    assert core_transient.is_transient_transport_error(
        IndexError("list index out of range")) is False
