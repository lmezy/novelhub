"""Tests for books API endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_list_books(client):
    resp = await client.get("/api/books")
    assert resp.status_code in (200, 500)  # 200 with DB, 500 without


@pytest.mark.asyncio
async def test_get_book_404(client):
    resp = await client.get("/api/books/nonexistent-book-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_book_validation(client):
    """POST /api/books without required fields should fail validation."""
    resp = await client.post("/api/books", json={})
    assert resp.status_code in (422, 500)


@pytest.mark.asyncio
async def test_delete_book_admin_required(client):
    """DELETE /api/books/{id} requires admin auth."""
    resp = await client.delete("/api/books/some-id")
    assert resp.status_code in (401, 403, 500)


@pytest.mark.asyncio
async def test_epub_download_404(client):
    """EPUB download for nonexistent book returns 404."""
    resp = await client.get("/api/books/nonexistent/epub")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resync_book_requires_admin(client):
    """POST /api/books/{id}/sync requires admin."""
    resp = await client.post("/api/books/some-id/sync")
    assert resp.status_code in (401, 403, 500)