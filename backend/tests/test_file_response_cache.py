"""Caching headers for the reader's file responses.

Starlette's ``FileResponse`` has no ``cache-control`` and no 304 path, so
without this every chapter image was re-downloaded in full on every visit.
"""

from pathlib import Path

import pytest
from starlette.requests import Request

from app.api.file_response import cached_file_response


def make_request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


@pytest.fixture()
def image(tmp_path: Path) -> Path:
    path = tmp_path / "f480db5f0f7705c2.webp"
    path.write_bytes(b"RIFF....WEBP" + b"x" * 512)
    return path


def test_chapter_images_are_cacheable_for_a_week(image: Path):
    response = cached_file_response(make_request(), image, max_age=604800, immutable=True)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=604800, immutable"
    assert response.headers["etag"]


def test_covers_revalidate_instead_of_being_cached_hard(image: Path):
    response = cached_file_response(make_request(), image, max_age=300)

    assert response.headers["cache-control"] == "private, max-age=300"
    assert "immutable" not in response.headers["cache-control"]


def test_matching_etag_returns_304_without_a_body(image: Path):
    first = cached_file_response(make_request(), image, max_age=604800, immutable=True)
    etag = first.headers["etag"]

    second = cached_file_response(
        make_request({"if-none-match": etag}), image, max_age=604800, immutable=True
    )

    assert second.status_code == 304
    assert second.headers["etag"] == etag
    assert second.headers["cache-control"] == "private, max-age=604800, immutable"


def test_etag_list_and_weak_etag_also_match(image: Path):
    etag = cached_file_response(make_request(), image, max_age=300).headers["etag"]

    for header in (f'"other", {etag}', "*", f"W/{etag}"):
        response = cached_file_response(
            make_request({"if-none-match": header}), image, max_age=300
        )
        assert response.status_code == 304, header


def test_a_stale_etag_still_gets_the_file(image: Path):
    response = cached_file_response(
        make_request({"if-none-match": '"nope"'}), image, max_age=300
    )

    assert response.status_code == 200


def test_if_modified_since_returns_304(image: Path):
    etag_response = cached_file_response(make_request(), image, max_age=300)
    last_modified = etag_response.headers["last-modified"]

    response = cached_file_response(
        make_request({"if-modified-since": last_modified}), image, max_age=300
    )

    assert response.status_code == 304
