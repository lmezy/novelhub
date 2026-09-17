"""AI reader-assistant tests (config resolution, transport, context assembly)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Book
from app.services.ai import AIService, parse_json_list, strip_markup
from app.services.ai_client import AIError, LLMClient, LLMResult, build_payload
from app.services.ai_config import (
    AIConfig,
    decrypt_secret,
    encrypt_secret,
    get_ai_config,
    resolve_ai_config,
    set_ai_config,
)
from app.services.rag import RAGService


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeStorage:
    def __init__(self, texts: dict[str, str]):
        self.texts = texts

    def read_chapter(self, path: str) -> str:
        return self.texts.get(path, "")


class FakeDB:
    """Minimal AsyncSession stand-in for the AI service.

    ``scalars`` applies the ``chapter_number`` bounds and ``LIMIT`` of the
    incoming ``select(Chapter)`` so chapter-window assertions test the real
    query the service builds.
    """

    def __init__(self, book=None, chapters=(), scalar_value=0):
        self.book = book
        self.chapters = list(chapters)
        self.queries: list = []
        self.scalar_value = scalar_value
        self.added: list = []
        self.commits = 0

    async def get(self, model, key):
        if model is Book:
            return self.book
        return None

    @staticmethod
    def _filtered(query, chapters):
        from sqlalchemy.sql import operators

        clause = getattr(query, "whereclause", None)
        criteria = list(getattr(clause, "clauses", [])) if clause is not None else []
        if clause is not None and not criteria:
            criteria = [clause]
        for criterion in criteria:
            left = getattr(criterion, "left", None)
            right = getattr(criterion, "right", None)
            if left is None or right is None:
                continue
            if getattr(left, "key", None) != "chapter_number":
                continue
            value = getattr(right, "value", None)
            if value is None:
                continue
            operator = criterion.operator
            if operator is operators.ge:
                chapters = [c for c in chapters if c.chapter_number >= value]
            elif operator is operators.le:
                chapters = [c for c in chapters if c.chapter_number <= value]
            elif operator is operators.gt:
                chapters = [c for c in chapters if c.chapter_number > value]
            elif operator is operators.lt:
                chapters = [c for c in chapters if c.chapter_number < value]
        limit_clause = getattr(query, "_limit_clause", None)
        limit = getattr(limit_clause, "value", None) if limit_clause is not None else None
        if isinstance(limit, int):
            chapters = chapters[:limit]
        return chapters

    async def scalars(self, query):
        self.queries.append(query)
        return self._filtered(query, list(self.chapters))

    async def scalar(self, query):
        self.queries.append(query)
        return self.scalar_value

    async def execute(self, *args, **kwargs):
        return None

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    def add(self, obj):
        self.added.append(obj)


class FakeLLM:
    def __init__(self, text="答案", prompt_tokens=11, completion_tokens=7):
        self.text = text
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.calls: list[list[dict]] = []

    async def chat(self, messages, max_tokens=None, temperature=None, retries=None):
        self.calls.append(list(messages))
        return LLMResult(
            self.text,
            model="fake-model",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )

    async def stream(self, messages, max_tokens=None, temperature=None, retries=None):
        self.calls.append(list(messages))
        for part in ("你", "好"):
            yield part


def make_book(**overrides):
    values = {
        "id": "b1",
        "title": "测试小说",
        "author_name": "某作者",
        "description": "简介",
        "is_r18": False,
        "is_public": True,
        "owner_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_chapters(count: int):
    return [
        SimpleNamespace(
            id=f"c{n}",
            chapter_number=n,
            title=f"第{n}章",
            content_path=f"p{n}",
        )
        for n in range(1, count + 1)
    ]


def make_service(chapters, *, book=None, config=None, texts=None):
    fake_db = FakeDB(book=book or make_book(), chapters=chapters)
    service = AIService(fake_db, config=config or configured_config())
    service.storage = FakeStorage(
        texts if texts is not None else {f"p{n}": f"正文{n}" for n in range(1, 60)}
    )
    return service, fake_db


def configured_config(**overrides) -> AIConfig:
    values = {
        "enabled": True,
        "provider": "openai",
        "api_key": "sk-test",
        "temperature": 0.2,
        "max_tokens": 1024,
        "context_chars": 8000,
        "rag_enabled": False,
        "rag_top_k": 3,
    }
    values.update(overrides)
    return AIConfig(**values)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_env_fallback_uses_provider_preset():
    cfg = resolve_ai_config({}, env={"AI_PROVIDER": "deepseek", "AI_API_KEY": "sk-x"})

    assert cfg.provider == "deepseek"
    assert cfg.effective_base_url == "https://api.deepseek.com/v1"
    assert cfg.effective_model == "deepseek-chat"
    assert cfg.configured is True


def test_stored_settings_override_environment():
    cfg = resolve_ai_config(
        {"ai_provider": "openai", "ai_model": "gpt-4o", "ai_api_key": "sk-db"},
        env={"AI_MODEL": "gpt-4o-mini", "AI_API_KEY": "sk-env"},
    )

    assert cfg.effective_model == "gpt-4o"
    assert cfg.api_key == "sk-db"


def test_keyless_provider_is_configured_without_a_key():
    cfg = resolve_ai_config({"ai_provider": "ollama"}, env={})

    assert cfg.needs_key is False
    assert cfg.enabled is True
    assert cfg.configured is True


def test_provider_without_key_is_not_configured():
    cfg = resolve_ai_config({"ai_provider": "deepseek", "ai_enabled": "true"}, env={})

    assert cfg.configured is False


def test_disabled_flag_wins_over_a_present_key():
    cfg = resolve_ai_config(
        {"ai_provider": "deepseek", "ai_api_key": "sk-x", "ai_enabled": "false"}, env={}
    )

    assert cfg.api_key == "sk-x"
    assert cfg.enabled is False
    assert cfg.configured is False


def test_claude_uses_the_anthropic_protocol():
    cfg = resolve_ai_config({"ai_provider": "claude", "ai_api_key": "k"}, env={})

    assert cfg.kind == "anthropic"
    url, payload = build_payload(
        cfg, [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    )
    assert url == "https://api.anthropic.com/v1/messages"
    assert payload["system"] == "sys"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_merges_consecutive_same_role_messages():
    cfg = resolve_ai_config({"ai_provider": "claude", "ai_api_key": "k"}, env={})

    _, payload = build_payload(
        cfg,
        [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
        ],
    )

    assert payload["messages"] == [
        {"role": "user", "content": "a\n\nb"},
        {"role": "assistant", "content": "c"},
    ]


def test_custom_base_url_is_not_rewritten_twice():
    cfg = resolve_ai_config(
        {"ai_provider": "custom", "ai_api_key": "k",
         "ai_base_url": "http://host:8000/v1/chat/completions"},
        env={},
    )

    url, _ = build_payload(cfg, [{"role": "user", "content": "x"}])

    assert url == "http://host:8000/v1/chat/completions"


def test_embedding_falls_back_to_the_chat_endpoint():
    cfg = resolve_ai_config({"ai_provider": "qwen", "ai_api_key": "k"}, env={})

    assert cfg.effective_embedding_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert cfg.effective_embedding_model == "text-embedding-v3"
    assert cfg.embeddings_supported is True


def test_provider_without_embeddings_reports_unsupported():
    cfg = resolve_ai_config({"ai_provider": "deepseek", "ai_api_key": "k"}, env={})

    assert cfg.effective_embedding_model == ""
    assert cfg.embeddings_supported is False


def test_api_key_round_trip_and_public_dict_hides_it():
    stored = encrypt_secret("sk-secret-value")
    assert stored.startswith("enc:")
    assert decrypt_secret(stored) == "sk-secret-value"

    cfg = resolve_ai_config({"ai_provider": "openai", "ai_api_key": stored}, env={})
    payload = cfg.public_dict()

    assert cfg.api_key == "sk-secret-value"
    assert payload["api_key_set"] is True
    assert "sk-secret-value" not in str(payload)
    assert payload["api_key_hint"].startswith("sk-")


def test_plaintext_legacy_key_still_works():
    cfg = resolve_ai_config({"ai_provider": "openai", "ai_api_key": "sk-plain"}, env={})

    assert cfg.api_key == "sk-plain"


@pytest.mark.asyncio
async def test_saving_settings_round_trips_through_the_database():
    from unittest.mock import MagicMock

    db = AsyncMock()
    db.scalars = AsyncMock(return_value=[])
    db.add = MagicMock()
    db.commit = AsyncMock()

    await set_ai_config(db, {
        "enabled": True,
        "provider": "deepseek",
        "api_key": "sk-live-key",
        "model": "deepseek-chat",
        "use_proxy": True,
    })

    written = {entry.args[0].key: entry.args[0].value for entry in db.add.call_args_list}
    assert written["ai_api_key"].startswith("enc:")
    assert "sk-live-key" not in written["ai_api_key"]
    assert written["ai_use_proxy"] == "true"

    db.scalars = AsyncMock(return_value=[entry.args[0] for entry in db.add.call_args_list])
    reloaded = await get_ai_config(db)

    assert reloaded.provider == "deepseek"
    assert reloaded.api_key == "sk-live-key"
    assert reloaded.use_proxy is True
    assert reloaded.configured is True


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", lines=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._text = text
        self._lines = list(lines or [])

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._text

    async def aread(self):
        return self._text.encode("utf-8")

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *exc):
        return False


class FakeHTTP:
    def __init__(self, response=None, responses=None):
        self._responses = list(responses or ([response] if response else []))
        self.posts: list[dict] = []
        self.streams: list[dict] = []
        self.last_stream = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self):
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    async def post(self, url, headers=None, json=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        return self._next()

    def stream(self, method, url, headers=None, json=None):
        self.streams.append({"method": method, "url": url, "headers": headers, "json": json})
        return FakeStreamContext(self._next())


def patch_http(monkeypatch, fake: FakeHTTP):
    monkeypatch.setattr(LLMClient, "_client", lambda self, timeout=None: fake)
    return fake


def test_tls_context_is_built_once_across_clients(monkeypatch):
    """A fresh httpx client per request must not re-parse the CA bundle.

    Building the default context on every request cost ~2.5s on Windows before
    ``default_ssl_context`` started caching it.
    """
    from app.services import ai_client

    calls = {"count": 0}
    real_create = ai_client.ssl.create_default_context

    def counting_create(*args, **kwargs):
        calls["count"] += 1
        return real_create(*args, **kwargs)

    monkeypatch.setattr(ai_client.ssl, "create_default_context", counting_create)
    monkeypatch.setattr(ai_client, "_ssl_context", None)

    client = LLMClient(configured_config())

    async def build_twice():
        first = client._client()
        second = client._client()
        await first.aclose()
        await second.aclose()

    asyncio.new_event_loop().run_until_complete(build_twice())

    assert calls["count"] == 1
    assert ai_client.default_ssl_context() is ai_client.default_ssl_context()


@pytest.mark.asyncio
async def test_chat_reports_usage_and_uses_bearer_auth(monkeypatch):
    fake = patch_http(monkeypatch, FakeHTTP(FakeResponse(payload={
        "model": "gpt-4o-mini",
        "choices": [{"message": {"content": " 你好 "}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
    })))
    cfg = configured_config()

    result = await LLMClient(cfg).chat([{"role": "user", "content": "hi"}])

    assert result.text == "你好"
    assert result.tokens_used == 16
    assert fake.posts[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert fake.posts[0]["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_chat_translates_401_into_an_actionable_error(monkeypatch):
    patch_http(monkeypatch, FakeHTTP(FakeResponse(
        status_code=401, text='{"error":{"message":"bad key"}}'
    )))
    cfg = configured_config()

    with pytest.raises(AIError) as excinfo:
        await LLMClient(cfg).chat([{"role": "user", "content": "hi"}], retries=0)

    assert "API Key" in str(excinfo.value)
    assert excinfo.value.status == 401


@pytest.mark.asyncio
async def test_chat_retries_transient_5xx(monkeypatch):
    fake = patch_http(monkeypatch, FakeHTTP(responses=[
        FakeResponse(status_code=503, text="upstream down"),
        FakeResponse(payload={"choices": [{"message": {"content": "ok"}}]}),
    ]))
    cfg = configured_config()

    with patch("app.services.ai_client.asyncio.sleep", new=AsyncMock()):
        result = await LLMClient(cfg).chat([{"role": "user", "content": "hi"}])

    assert result.text == "ok"
    assert len(fake.posts) == 2


@pytest.mark.asyncio
async def test_connection_error_mentions_the_proxy(monkeypatch):
    import httpx

    class Boom(FakeHTTP):
        async def post(self, url, headers=None, json=None):
            raise httpx.ConnectError("connection refused")

    patch_http(monkeypatch, Boom())
    cfg = configured_config(
        provider="openai", use_proxy=True, proxy_url="http://127.0.0.1:27890"
    )

    with patch("app.services.ai_client.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(AIError) as excinfo:
            await LLMClient(cfg).chat([{"role": "user", "content": "hi"}], retries=0)

    assert "127.0.0.1:27890" in str(excinfo.value)


@pytest.mark.asyncio
async def test_stream_parses_openai_sse_deltas(monkeypatch):
    fake = patch_http(monkeypatch, FakeHTTP(FakeResponse(lines=[
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        "data: [DONE]",
    ])))
    cfg = configured_config()

    parts = [chunk async for chunk in LLMClient(cfg).stream([{"role": "user", "content": "hi"}])]

    assert parts == ["你", "好"]
    assert fake.streams[0]["json"]["stream"] is True


@pytest.mark.asyncio
async def test_stream_parses_anthropic_events(monkeypatch):
    patch_http(monkeypatch, FakeHTTP(FakeResponse(lines=[
        "event: content_block_delta",
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"嗨"}}',
        'data: {"type":"message_stop"}',
    ])))
    cfg = resolve_ai_config({"ai_provider": "claude", "ai_api_key": "k"}, env={})

    parts = [chunk async for chunk in LLMClient(cfg).stream([{"role": "user", "content": "hi"}])]

    assert parts == ["嗨"]


@pytest.mark.asyncio
async def test_embed_rejects_providers_without_embeddings():
    cfg = configured_config(provider="deepseek")

    with pytest.raises(AIError) as excinfo:
        await LLMClient(cfg).embed(["文本"])

    assert "向量" in str(excinfo.value)


@pytest.mark.asyncio
async def test_embed_orders_vectors_by_index(monkeypatch):
    fake = patch_http(monkeypatch, FakeHTTP(FakeResponse(payload={"data": [
        {"index": 1, "embedding": [0.0, 1.0]},
        {"index": 0, "embedding": [1.0, 0.0]},
    ]})))
    cfg = configured_config(provider="qwen")

    vectors = await LLMClient(cfg).embed(["a", "b"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert fake.posts[0]["url"].endswith("/embeddings")


# ---------------------------------------------------------------------------
# JSON parsing / markup
# ---------------------------------------------------------------------------


def test_parse_json_list_unwraps_fences_and_prose():
    items, parsed = parse_json_list('结果如下：\n```json\n[{"name":"林岚","role":"主角"}]\n```\n以上。')

    assert parsed is True
    assert items == [{"name": "林岚", "role": "主角"}]


def test_parse_json_list_accepts_a_single_object():
    items, parsed = parse_json_list('{"name":"甲"}')

    assert parsed is True
    assert items == [{"name": "甲"}]


def test_parse_json_list_falls_back_to_prose():
    items, parsed = parse_json_list("抱歉，我无法完成这个请求。")

    assert parsed is False
    assert items[0]["description"] == "抱歉，我无法完成这个请求。"


def test_strip_markup_removes_images_and_title():
    cleaned = strip_markup("# 第一章\n\n![封面](http://x/1.jpg)\n\n正文段落\n\n\n\n第二段")

    assert "![封面]" not in cleaned
    assert cleaned.startswith("正文段落")
    assert "\n\n\n" not in cleaned


# ---------------------------------------------------------------------------
# context assembly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_centres_on_the_current_chapter():
    chapters = make_chapters(30)
    service, db = make_service(chapters)

    bundle = await service.build_context(
        "b1", question="他为什么离开？", chapter_number=10, span=5, mode="window"
    )

    params = db.queries[-1].compile().params
    bounds = {value for value in params.values() if isinstance(value, int)}
    assert {8, 12} <= bounds
    assert bundle.mode == "window"
    assert [s["chapter_number"] for s in bundle.sources] == [8, 9, 10, 11, 12]


@pytest.mark.asyncio
async def test_chat_returns_sources_and_real_token_counts():
    service, _ = make_service(make_chapters(20))
    fake = FakeLLM("因为家族变故。")
    service._client = AsyncMock(return_value=fake)

    result = await service.chat("b1", "他为什么离开？", chapter_number=5, mode="window")

    assert result["answer"] == "因为家族变故。"
    assert result["tokens_used"] == 18
    assert result["context_mode"] == "window"
    assert [s["chapter_number"] for s in result["sources"]] == [3, 4, 5, 6, 7]
    # The prompt carries book metadata and the reader's question.
    prompt = fake.calls[0][-1]["content"]
    assert "测试小说" in prompt
    assert "他为什么离开？" in prompt


@pytest.mark.asyncio
async def test_chat_replays_but_bounds_history():
    service, _ = make_service(make_chapters(5))
    fake = FakeLLM("ok")
    service._client = AsyncMock(return_value=fake)
    history = [{"role": "user", "content": f"q{i}"} for i in range(20)]

    await service.chat("b1", "继续", history=history, mode="window")

    roles = [m["role"] for m in fake.calls[0]]
    assert roles[0] == "system"
    assert len(roles) == 1 + 10 + 1


@pytest.mark.asyncio
async def test_chat_without_position_falls_back_to_the_first_chapters():
    service, _ = make_service(make_chapters(20))
    service._client = AsyncMock(return_value=FakeLLM("ok"))

    result = await service.chat("b1", "讲什么？", mode="window")

    assert result["context_mode"] == "head"
    assert result["sources"][0]["chapter_number"] == 1


@pytest.mark.asyncio
async def test_unconfigured_service_refuses_with_a_hint():
    service, _ = make_service(make_chapters(3), config=AIConfig(enabled=False))

    with pytest.raises(AIError) as excinfo:
        await service.chat("b1", "hi")

    assert "设置" in str(excinfo.value)


@pytest.mark.asyncio
async def test_stream_chat_emits_sources_deltas_and_done():
    service, _ = make_service(make_chapters(6))
    service._client = AsyncMock(return_value=FakeLLM())

    events = [event async for event in service.stream_chat(
        "b1", "问题", chapter_number=2, mode="window"
    )]

    assert events[0]["type"] == "sources"
    assert [e["text"] for e in events if e["type"] == "delta"] == ["你", "好"]
    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == "你好"


@pytest.mark.asyncio
async def test_stream_chat_reports_configuration_errors_as_events():
    service, _ = make_service(make_chapters(3), config=AIConfig(enabled=False))

    events = [event async for event in service.stream_chat("b1", "问题")]

    assert events == [{"type": "error", "message":
                       events[0]["message"]}]
    assert "AI" in events[0]["message"]


# ---------------------------------------------------------------------------
# summary / analysis / transform
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_maps_in_batches_then_reduces():
    service, _ = make_service(make_chapters(8))
    fake = FakeLLM("分段摘要")
    service._client = AsyncMock(return_value=fake)

    result = await service.summarize("b1")

    # 8 chapters -> 2 batches of 6 and 2, then one reduce call.
    assert len(fake.calls) == 3
    assert result["chapters_covered"] == 8
    assert result["total_chapters"] == 8
    assert result["sampled"] is False
    assert result["chapter_start"] == 1 and result["chapter_end"] == 8


@pytest.mark.asyncio
async def test_summarize_samples_a_huge_book():
    service, _ = make_service(make_chapters(300))
    fake = FakeLLM("摘要")
    service._client = AsyncMock(return_value=fake)

    result = await service.summarize("b1", max_chapters=12)

    assert result["sampled"] is True
    assert result["chapters_covered"] == 12
    assert result["total_chapters"] == 300


@pytest.mark.asyncio
async def test_summarize_rejects_an_empty_range():
    service, _ = make_service([])
    service._client = AsyncMock(return_value=FakeLLM())

    with pytest.raises(ValueError):
        await service.summarize("b1", chapter_start=50, chapter_end=60)


@pytest.mark.asyncio
async def test_characters_parses_fenced_json():
    service, _ = make_service(make_chapters(10))
    service._client = AsyncMock(return_value=FakeLLM(
        '```json\n[{"name":"林岚","role":"主角","description":"剑客"}]\n```'
    ))

    result = await service.analyze_characters("b1")

    assert result["parsed"] is True
    assert result["characters"][0]["name"] == "林岚"
    assert result["chapters_scanned"] == 10


@pytest.mark.asyncio
async def test_timeline_reports_unparsed_prose_instead_of_dropping_it():
    service, _ = make_service(make_chapters(4))
    service._client = AsyncMock(return_value=FakeLLM("第一章：主角出场。"))

    result = await service.extract_timeline("b1")

    assert result["parsed"] is False
    assert result["raw"].startswith("第一章")


@pytest.mark.asyncio
async def test_transform_runs_the_requested_action():
    service, _ = make_service(make_chapters(3))
    fake = FakeLLM("白话解释")
    service._client = AsyncMock(return_value=fake)

    result = await service.transform("落霞与孤鹜齐飞", "explain",
                                     book_id="b1", chapter_number=3)

    assert result["action"] == "explain"
    assert result["result"] == "白话解释"
    assert "落霞与孤鹜齐飞" in fake.calls[0][-1]["content"]


@pytest.mark.asyncio
async def test_transform_validates_input():
    service, _ = make_service(make_chapters(3))
    service._client = AsyncMock(return_value=FakeLLM())

    with pytest.raises(ValueError):
        await service.transform("文字", "nonsense")
    with pytest.raises(ValueError):
        await service.transform("   ", "explain")


@pytest.mark.asyncio
async def test_stream_transform_reports_errors_as_events():
    service, _ = make_service(make_chapters(3))
    service._client = AsyncMock(side_effect=AIError("AI 未配置。"))

    events = [e async for e in service.stream_transform("文字", "explain")]

    assert events[0]["type"] == "error"
    assert "AI" in events[0]["message"]


@pytest.mark.asyncio
async def test_stream_transform_emits_start_deltas_and_done():
    service, _ = make_service(make_chapters(3))
    service._client = AsyncMock(return_value=FakeLLM())

    events = [e async for e in service.stream_transform("文字", "polish")]

    assert events[0]["type"] == "start"
    assert events[0]["action"] == "polish"
    assert [e["text"] for e in events if e["type"] == "delta"] == ["你", "好"]
    assert events[-1] == {"type": "done", "result": "你好", "model": "gpt-4o-mini"}


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------


def test_chunk_text_overlaps_and_respects_the_cap():
    service = RAGService(FakeDB(), config=configured_config())
    text = "字" * 3000

    chunks = service.chunk_text(text)

    assert len(chunks) > 1
    assert all(chunk for chunk in chunks)
    assert len(service.chunk_text("   ")) == 0


def test_rank_orders_by_cosine_similarity():
    service = RAGService(FakeDB(), config=configured_config(provider="qwen"))
    rows = [
        SimpleNamespace(chapter_id="c1", chunk_index=0, content="far",
                        embedding=[0.0, 1.0]),
        SimpleNamespace(chapter_id="c2", chunk_index=1, content="near",
                        embedding=[1.0, 0.0]),
    ]

    ranked = service._rank([1.0, 0.0], rows)

    assert [item["chapter_id"] for item in ranked] == ["c2", "c1"]
    assert ranked[0]["similarity"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_rag_refuses_without_an_embedding_provider():
    service = RAGService(FakeDB(), config=configured_config(provider="deepseek"))

    with pytest.raises(AIError) as excinfo:
        await service.generate_embeddings(["文本"])

    assert "Embedding" in str(excinfo.value) or "向量" in str(excinfo.value)


@pytest.mark.asyncio
async def test_search_does_not_embed_for_an_unindexed_book():
    db = FakeDB()
    service = RAGService(db, config=configured_config(provider="qwen"))
    service._client = AsyncMock()

    assert await service.search("b1", "剑") == []
    service._client.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_index_status_counts_chunks_and_chapters():
    db = FakeDB()
    db.scalar_value = 7
    service = RAGService(db, config=configured_config(provider="qwen"))

    status = await service.index_status("b1")

    assert status["chunks"] == 7
    assert status["indexed"] is True
    assert status["embeddings_supported"] is True
