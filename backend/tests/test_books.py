import pytest

@pytest.mark.asyncio
async def test_list_books(client):
    resp = await client.get('/api/books')
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_get_book_404(client):
    resp = await client.get('/api/books/nonexistent')
    assert resp.status_code == 404
