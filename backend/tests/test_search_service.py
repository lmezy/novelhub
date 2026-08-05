from unittest.mock import MagicMock, patch

from app.services.search import SearchService


def _make_service():
    client = MagicMock()
    with patch("app.services.search.meilisearch.Client", return_value=client):
        service = SearchService()
    return service, client


def test_ensure_index_is_cached():
    service, client = _make_service()

    service._ensure_index("books")
    service._ensure_index("books")

    assert client.get_index.call_count == 1
    assert client.index.call_count == 1


def test_chapter_buffer_flushes_in_batches():
    service, client = _make_service()
    index = client.index.return_value

    for i in range(105):
        service.buffer_chapter({"id": str(i)})

    assert index.add_documents.call_count == 1

    service.flush_chapters()

    assert index.add_documents.call_count == 2
