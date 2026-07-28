"""S3 and WebDAV storage backends for NovelHub.

Configure via STORAGE_BACKEND env var: local | s3 | webdav
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.services.storage import BookStorage, safe_segment


class S3BookStorage(BookStorage):
    """Store books in S3-compatible object storage."""

    def __init__(self, root: str | None = None):
        super().__init__(root)
        import boto3
        self.endpoint = os.getenv("S3_ENDPOINT", "")
        self.bucket = os.getenv("S3_BUCKET", "novelhub-books")
        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.endpoint or None,
            aws_access_key_id=os.getenv("S3_ACCESS_KEY", ""),
            aws_secret_access_key=os.getenv("S3_SECRET_KEY", ""),
        )

    def _s3_key(self, author: str, title: str, filename: str = "") -> str:
        key = f"{safe_segment(author)}/{safe_segment(title)}"
        return f"{key}/{filename}" if filename else key

    def book_dir(self, author: str, title: str) -> Path:
        return Path(f"s3://{self.bucket}/{self._s3_key(author, title)}")

    def write_metadata(self, author: str, title: str, metadata: dict) -> Path:
        key = self._s3_key(author, title, "metadata.json")
        self.s3.put_object(
            Bucket=self.bucket, Key=key,
            Body=json.dumps(metadata, ensure_ascii=False, indent=2),
            ContentType="application/json",
        )
        return self.book_dir(author, title) / "metadata.json"

    def write_chapter(self, author: str, title: str, number: int,
                       chapter_title: str, content: str) -> tuple[str, str]:
        key = self._s3_key(author, title, f"{number:06d}.md")
        NL = chr(10)
        markdown = f"# {chapter_title}{NL}{NL}{content.strip()}{NL}"
        body = markdown.encode("utf-8")
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=body,
                            ContentType="text/markdown; charset=utf-8")
        content_hash = hashlib.sha256(body).hexdigest()
        return f"s3://{self.bucket}/{key}", content_hash

    def read_chapter(self, content_path: str) -> str:
        parts = content_path.replace("s3://", "").split("/", 1)
        resp = self.s3.get_object(Bucket=parts[0], Key=parts[1])
        return resp["Body"].read().decode("utf-8")


class WebDAVBookStorage(BookStorage):
    """Store books on a WebDAV server."""

    def __init__(self, root: str | None = None):
        super().__init__(root)
        self.base_url = os.getenv("WEBDAV_URL", "").rstrip("/")
        self.username = os.getenv("WEBDAV_USER", "")
        self.password = os.getenv("WEBDAV_PASS", "")

    def _url(self, author: str, title: str, filename: str = "") -> str:
        path = f"{safe_segment(author)}/{safe_segment(title)}"
        if filename:
            path += f"/{filename}"
        return f"{self.base_url}/{path}"

    def _mkcol(self, url: str) -> None:
        import httpx
        auth = httpx.BasicAuth(self.username, self.password) if self.username else None
        parts = url.replace(self.base_url + "/", "").split("/")
        cumulative = self.base_url
        for part in parts[:-1]:
            cumulative += "/" + part
            try:
                httpx.Client(auth=auth, timeout=10).request("MKCOL", cumulative)
            except Exception:
                pass

    def _webdav_req(self, method: str, url: str, data: bytes | None = None) -> bytes:
        import httpx
        auth = httpx.BasicAuth(self.username, self.password) if self.username else None
        resp = httpx.Client(auth=auth, timeout=30).request(method, url, content=data)
        resp.raise_for_status()
        return resp.content if method == "GET" else b""

    def book_dir(self, author: str, title: str) -> Path:
        return Path(self._url(author, title))

    def write_metadata(self, author: str, title: str, metadata: dict) -> Path:
        url = self._url(author, title, "metadata.json")
        self._mkcol(url)
        body = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
        self._webdav_req("PUT", url, body)
        return self.book_dir(author, title) / "metadata.json"

    def write_chapter(self, author: str, title: str, number: int,
                       chapter_title: str, content: str) -> tuple[str, str]:
        url = self._url(author, title, f"{number:06d}.md")
        self._mkcol(url)
        NL = chr(10)
        markdown = f"# {chapter_title}{NL}{NL}{content.strip()}{NL}"
        body = markdown.encode("utf-8")
        self._webdav_req("PUT", url, body)
        content_hash = hashlib.sha256(body).hexdigest()
        return url, content_hash

    def read_chapter(self, content_path: str) -> str:
        return self._webdav_req("GET", content_path).decode("utf-8")


def create_storage() -> BookStorage:
    backend = os.getenv("STORAGE_BACKEND", "local").lower()
    if backend == "s3":
        return S3BookStorage()
    elif backend == "webdav":
        return WebDAVBookStorage()
    return BookStorage()
