"""API-level tests for the AI / RAG routes and the admin AI settings."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.ai_client import AIError
from app.services.auth import get_current_user, require_admin
from app.core.database import get_db


def fake_user(role="super_admin"):
    return SimpleNamespace(
        id="u1", role=role, r18_enabled=True, non_r18_enabled=True,
    )


def fake_book(**overrides):
    values = {
        "id": "b1",
        "title": "测试小说",
        "is_r18": False,
        "is_public": True,
        "owner_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_db(*, book=None, rows=None, scalar_value=0):
    db = AsyncMock()
    db.get = AsyncMock(return_value=book)
    db.scalars = AsyncMock(return_value=list(rows or []))
    db.scalar = AsyncMock(return_value=scalar_value)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    return db


async def call(method, path, db, *, user=None, json=None, admin=True):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: (user or fake_user())
    if admin:
        app.dependency_overrides[require_admin] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=json)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_reports_why_ai_is_unavailable():
    resp = await call("GET", "/api/ai/status", fake_db())

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "设置" in body["reason"]


@pytest.mark.asyncio
async def test_status_reports_the_configured_provider():
    rows = [
        SimpleNamespace(key="ai_enabled", value="true"),
        SimpleNamespace(key="ai_provider", value="deepseek"),
        SimpleNamespace(key="ai_api_key", value="sk-live"),
        SimpleNamespace(key="ai_model", value="deepseek-chat"),
    ]

    resp = await call("GET", "/api/ai/status", fake_db(rows=rows))

    body = resp.json()
    assert body["available"] is True
    assert body["provider"] == "deepseek"
    assert body["effective_model"] == "deepseek-chat"
    assert "sk-live" not in resp.text


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_forwards_the_reading_position_and_returns_sources():
    db = fake_db(book=fake_book())
    payload = {
        "book_id": "b1",
        "message": "他为什么离开？",
        "chapter_number": 12,
        "context_chapters": 6,
        "history": [{"role": "user", "content": "之前问过"}],
    }
    result = {
        "answer": "因为家族变故。",
        "model": "deepseek-chat",
        "tokens_used": 42,
        "provider": "deepseek",
        "context_mode": "window",
        "sources": [{"chapter_id": "c12", "chapter_number": 12, "title": "决裂"}],
    }
    service = MagicMock()
    service.chat = AsyncMock(return_value=result)
    with patch("app.api.routes.ai.AIService", return_value=service):
        resp = await call("POST", "/api/ai/chat", db, json=payload)

    assert resp.status_code == 200
    assert resp.json()["sources"][0]["chapter_number"] == 12
    kwargs = service.chat.await_args.kwargs
    assert kwargs["chapter_number"] == 12
    assert kwargs["context_chapters"] == 6
    assert kwargs["history"] == [{"role": "user", "content": "之前问过"}]


@pytest.mark.asyncio
async def test_chat_returns_a_readable_502_when_the_provider_fails():
    db = fake_db(book=fake_book())
    service = MagicMock()
    service.chat = AsyncMock(side_effect=AIError("AI 服务拒绝了这个 API Key（HTTP 401）。", status=401))
    with patch("app.api.routes.ai.AIService", return_value=service):
        resp = await call("POST", "/api/ai/chat", db,
                          json={"book_id": "b1", "message": "hi"})

    assert resp.status_code == 502
    assert "API Key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_chat_rejects_a_book_the_user_cannot_see():
    db = fake_db(book=None)

    resp = await call("POST", "/api/ai/chat", db,
                      json={"book_id": "missing", "message": "hi"})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_stream_emits_sse_frames():
    db = fake_db(book=fake_book())

    async def fake_stream(self, **kwargs):
        yield {"type": "sources", "context_mode": "window", "sources": [
            {"chapter_id": "c1", "chapter_number": 1, "title": "开端"}]}
        yield {"type": "delta", "text": "你"}
        yield {"type": "delta", "text": "好"}
        yield {"type": "done", "model": "m", "answer": "你好"}

    with patch("app.api.routes.ai.AIService.stream_chat", fake_stream):
        resp = await call("POST", "/api/ai/chat/stream", db,
                          json={"book_id": "b1", "message": "hi"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-accel-buffering"] == "no"
    assert '"type": "delta"' in resp.text
    assert resp.text.rstrip().endswith('data: {"type": "end"}')


# ---------------------------------------------------------------------------
# summary / characters / timeline / transform
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_route_passes_the_range():
    db = fake_db(book=fake_book())
    service = MagicMock()
    service.summarize = AsyncMock(return_value={
        "summary": "概括", "chapters_covered": 5, "model": "m",
        "total_chapters": 9, "sampled": False, "provider": "openai",
    })
    with patch("app.api.routes.ai.AIService", return_value=service):
        resp = await call("POST", "/api/ai/summary", db, json={
            "book_id": "b1", "chapter_start": 3, "chapter_end": 7, "style": "brief",
        })

    assert resp.status_code == 200
    assert service.summarize.await_args.kwargs["chapter_start"] == 3
    assert service.summarize.await_args.kwargs["style"] == "brief"


@pytest.mark.asyncio
async def test_transform_route_rejects_empty_selection():
    db = fake_db(book=fake_book())
    service = MagicMock()
    service.transform = AsyncMock(side_effect=ValueError("请先选中一段文字。"))
    with patch("app.api.routes.ai.AIService", return_value=service):
        resp = await call("POST", "/api/ai/transform", db,
                          json={"text": " ", "action": "explain"})

    assert resp.status_code == 400
    assert "选中" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_transform_route_returns_the_action_result():
    db = fake_db(book=fake_book())
    service = MagicMock()
    service.transform = AsyncMock(return_value={
        "action": "translate", "result": "Hello", "model": "m",
        "provider": "openai", "tokens_used": 9,
    })
    with patch("app.api.routes.ai.AIService", return_value=service):
        resp = await call("POST", "/api/ai/transform", db, json={
            "text": "你好", "action": "translate", "book_id": "b1",
            "chapter_number": 2, "target_language": "English",
        })

    assert resp.status_code == 200
    assert resp.json()["result"] == "Hello"
    assert service.transform.await_args.kwargs["target_language"] == "English"


@pytest.mark.asyncio
async def test_transform_stream_emits_deltas():
    db = fake_db(book=fake_book())

    async def fake_stream(self, **kwargs):
        yield {"type": "start", "action": "translate"}
        yield {"type": "delta", "text": "Hello"}
        yield {"type": "done", "result": "Hello"}

    with patch("app.api.routes.ai.AIService.stream_transform", fake_stream):
        resp = await call("POST", "/api/ai/transform/stream", db,
                          json={"text": "你好", "action": "translate", "book_id": "b1"})

    assert resp.status_code == 200
    assert resp.headers["x-accel-buffering"] == "no"
    assert '"text": "Hello"' in resp.text


@pytest.mark.asyncio
async def test_characters_route_keeps_unparsed_prose():
    db = fake_db(book=fake_book())
    service = MagicMock()
    service.analyze_characters = AsyncMock(return_value={
        "characters": [{"name": "", "description": "我做不到"}],
        "model": "m", "provider": "openai", "parsed": False, "raw": "我做不到",
        "tokens_used": 3, "chapters_scanned": 2,
    })
    with patch("app.api.routes.ai.AIService", return_value=service):
        resp = await call("POST", "/api/ai/person", db, json={"book_id": "b1"})

    assert resp.status_code == 200
    assert resp.json()["parsed"] is False


# ---------------------------------------------------------------------------
# admin settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_get_ai_returns_presets_and_never_the_key():
    rows = [
        SimpleNamespace(key="ai_provider", value="qwen"),
        SimpleNamespace(key="ai_api_key", value="sk-secret"),
    ]
    resp = await call("GET", "/api/admin/ai", fake_db(rows=rows))

    body = resp.json()
    assert resp.status_code == 200
    assert body["api_key_set"] is True
    assert "sk-secret" not in resp.text
    assert any(p["value"] == "deepseek" for p in body["providers"])
    assert body["crawler_proxy"]["url"] == "" or isinstance(body["crawler_proxy"]["url"], str)


@pytest.mark.asyncio
async def test_admin_update_ai_encrypts_the_key():
    db = fake_db()

    resp = await call("PUT", "/api/admin/ai", db, json={
        "enabled": True,
        "provider": "deepseek",
        "api_key": "sk-secret-value",
        "model": "deepseek-chat",
    })

    assert resp.status_code == 200
    stored = {entry.args[0].key: entry.args[0].value for entry in db.add.call_args_list}
    assert stored["ai_api_key"].startswith("enc:")
    assert "sk-secret-value" not in stored["ai_api_key"]
    assert "sk-secret-value" not in resp.text
    assert stored["ai_provider"] == "deepseek"
    assert stored["ai_enabled"] == "true"


@pytest.mark.asyncio
async def test_admin_update_ai_keeps_an_unchanged_key():
    db = fake_db()

    await call("PUT", "/api/admin/ai", db, json={"api_key": "********"})

    stored = {entry.args[0].key: entry.args[0].value for entry in db.add.call_args_list}
    assert "ai_api_key" not in stored


@pytest.mark.asyncio
async def test_admin_update_ai_validates_ranges():
    resp = await call("PUT", "/api/admin/ai", fake_db(), json={"temperature": 5})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_test_ai_reports_provider_failure():
    db = fake_db()
    client = MagicMock()
    client.test_connection = AsyncMock(side_effect=AIError("无法连接 AI 服务（ConnectError）。"))
    with patch("app.api.routes.admin.LLMClient", return_value=client):
        resp = await call("POST", "/api/admin/ai/test?test_embeddings=false", db)

    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert "无法连接" in body["chat"]["error"]


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_status_requires_a_visible_book():
    resp = await call("GET", "/api/rag/status/b1", fake_db(book=None))

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rag_status_returns_index_state():
    db = fake_db(book=fake_book(), scalar_value=4)
    resp = await call("GET", "/api/rag/status/b1", db)

    assert resp.status_code == 200
    assert resp.json()["chunks"] == 4


@pytest.mark.asyncio
async def test_rag_search_reports_a_missing_embedding_provider():
    db = fake_db(book=fake_book())
    service = MagicMock()
    service.search = AsyncMock(side_effect=AIError("RAG 语义检索需要一个支持向量化的服务"))
    with patch("app.api.routes.rag.RAGService", return_value=service):
        resp = await call("POST", "/api/rag/search", db,
                          json={"book_id": "b1", "query": "剑"})

    assert resp.status_code == 502
    assert "向量" in resp.json()["detail"]
