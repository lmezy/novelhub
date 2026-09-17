"""File responses with the caching headers the reader actually needs.

``starlette.responses.FileResponse`` sends ``etag`` and ``last-modified`` but
implements neither ``if-none-match`` nor a ``cache-control`` header -- it only
understands ``Range``.  The result on the reading path was:

* no ``cache-control``, so the browser had nothing to cache against;
* no 304 path, so a conditional request still transferred the whole body.

Chapter images are 31.5 GB across 47,300 files with a 14 MB tail, so re-opening
a comic page or stepping back through chapters re-downloaded megabytes every
time.  This module adds both halves.

Chapter images are safe to cache hard: :meth:`BookStorage.save_chapter_image`
names each file after a hash of its source URL and never overwrites an existing
file, so the bytes behind a name never change.  Covers *can* change (a re-sync
overwrites the file), so they get a short max-age plus a working 304 instead.
"""

from pathlib import Path
import os

from fastapi import Request
from fastapi.responses import FileResponse, Response

__all__ = ["cached_file_response"]


def _etag_matches(header: str, etag: str) -> bool:
    """``If-None-Match`` may be ``*`` or a comma-separated list of etags."""
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate and candidate.removeprefix("W/") == etag.removeprefix("W/"):
            return True
    return False


def cached_file_response(
    request: Request,
    path: Path | str,
    *,
    max_age: int,
    immutable: bool = False,
) -> Response:
    """Serve a file with ``cache-control`` and a working ``304``.

    ``immutable`` promises the bytes behind the URL never change; only set it
    for content-addressed files such as chapter images.
    """
    # Passing stat_result means Starlette sets etag/last-modified/content-length
    # right here (and does not stat the file a second time when sending it).
    response = FileResponse(path, stat_result=os.stat(path))
    cache_control = f"private, max-age={max_age}"
    if immutable:
        cache_control += ", immutable"
    response.headers["cache-control"] = cache_control

    etag = response.headers.get("etag", "")
    # ``last-modified`` is compared as the string the client echoes back: it is
    # the value we formatted from the file's mtime, and a date comparison would
    # be off by the sub-second part of that mtime.
    last_modified = response.headers.get("last-modified", "")

    not_modified = False
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and etag:
        not_modified = _etag_matches(if_none_match, etag)
    elif last_modified:
        not_modified = request.headers.get("if-modified-since") == last_modified

    if not not_modified:
        return response

    headers = {"cache-control": cache_control}
    if etag:
        headers["etag"] = etag
    if last_modified:
        headers["last-modified"] = last_modified
    return Response(status_code=304, headers=headers)
