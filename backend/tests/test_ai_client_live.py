"""End-to-end transport tests against a real local HTTP server.

The mocked tests in ``test_ai_service.py`` verify prompt/response handling but
cannot prove how httpx actually behaves: whether the SSE body is delivered
incrementally, whether ``stream=True`` reaches the wire, and whether the
configured proxy is really used.  This module starts a tiny OpenAI-compatible
server on an ephemeral port and drives :class:`LLMClient` against it.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.services.ai_client import AIError, LLMClient
from app.services.ai_config import AIConfig

RECEIVED: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # keep pytest output clean
        return

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def do_POST(self):  # noqa: N802 - http.server API
        payload = self._body()
        RECEIVED.append({
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "api_key": self.headers.get("x-api-key"),
            "payload": payload,
        })

        if self.path.endswith("/embeddings"):
            body = json.dumps({
                "data": [
                    {"index": i, "embedding": [float(i), 0.5]}
                    for i, _ in enumerate(payload.get("input") or [])
                ]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ("流", "式", "回答"):
                frame = json.dumps({"choices": [{"delta": {"content": piece}}]})
                self.wfile.write(f"data: {frame}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        body = json.dumps({
            "model": payload.get("model"),
            "choices": [{"message": {"content": "一次性回答"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def llm_server():
    # Module-scoped: ``HTTPServer.__init__`` calls ``socket.getfqdn()``, which
    # costs seconds on a Windows box with a slow resolver -- pay it once.
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _clear_received():
    RECEIVED.clear()
    yield


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def config_for(base_url: str, **overrides) -> AIConfig:
    values = {
        "enabled": True,
        "provider": "custom",
        "base_url": base_url,
        "api_key": "sk-live",
        "model": "test-model",
        "timeout": 10.0,
        "embedding_provider": "custom",
        "embedding_model": "test-embed",
    }
    values.update(overrides)
    return AIConfig(**values)


@pytest.mark.asyncio
async def test_chat_over_a_real_socket(llm_server):
    result = await LLMClient(config_for(llm_server)).chat(
        [{"role": "user", "content": "你好"}], retries=0
    )

    assert result.text == "一次性回答"
    assert result.tokens_used == 10
    request = RECEIVED[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["auth"] == "Bearer sk-live"
    assert request["payload"]["model"] == "test-model"


@pytest.mark.asyncio
async def test_stream_is_delivered_incrementally(llm_server):
    parts = [
        chunk async for chunk in LLMClient(config_for(llm_server)).stream(
            [{"role": "user", "content": "你好"}], retries=0
        )
    ]

    assert parts == ["流", "式", "回答"]
    assert RECEIVED[0]["payload"]["stream"] is True


@pytest.mark.asyncio
async def test_embeddings_over_a_real_socket(llm_server):
    vectors = await LLMClient(config_for(llm_server)).embed(["a", "b"], retries=0)

    assert vectors == [[0.0, 0.5], [1.0, 0.5]]
    assert RECEIVED[0]["path"] == "/v1/embeddings"


@pytest.mark.asyncio
async def test_configured_proxy_is_actually_used(llm_server):
    """With proxying on, the request must not reach the target directly."""
    proxy = f"http://127.0.0.1:{free_port()}"

    with pytest.raises(AIError) as excinfo:
        await LLMClient(config_for(llm_server, use_proxy=True,
                                   proxy_url=proxy)).chat(
            [{"role": "user", "content": "你好"}], retries=0
        )

    assert proxy in str(excinfo.value)
    assert RECEIVED == []


@pytest.mark.asyncio
async def test_anthropic_chat_over_a_real_socket(llm_server):
    cfg = config_for(llm_server, provider="claude", model="claude-test")

    await LLMClient(cfg).chat([{"role": "system", "content": "sys"},
                               {"role": "user", "content": "hi"}], retries=0)

    request = RECEIVED[0]
    assert request["path"] == "/v1/messages"
    assert request["api_key"] == "sk-live"
    assert request["auth"] is None
    assert request["payload"]["system"] == "sys"


# ---------------------------------------------------------------------------
# service -> client -> wire
# ---------------------------------------------------------------------------


class _InlineDB:
    """Just enough AsyncSession for ``AIService``."""

    def __init__(self, book, chapters):
        self.book = book
        self.chapters = chapters

    async def get(self, model, key):
        return self.book

    async def scalars(self, query):
        return list(self.chapters)

    async def scalar(self, query):
        return 0

    async def execute(self, *args, **kwargs):
        return None

    async def flush(self):
        return None


class _InlineStorage:
    def __init__(self, texts):
        self.texts = texts

    def read_chapter(self, path):
        return self.texts.get(path, "")


@pytest.mark.asyncio
async def test_ai_service_sends_the_reading_window_to_the_provider(llm_server):
    from types import SimpleNamespace

    from app.services.ai import AIService

    chapters = [
        SimpleNamespace(id=f"c{n}", chapter_number=n, title=f"第{n}章",
                        content_path=f"p{n}")
        for n in range(1, 6)
    ]
    book = SimpleNamespace(id="b1", title="测试小说", author_name="作者",
                           description="简介")
    db = _InlineDB(book, chapters)
    service = AIService(db, config=config_for(llm_server, rag_enabled=False))
    service.storage = _InlineStorage({f"p{n}": f"这是第{n}章的正文内容" for n in range(1, 6)})

    result = await service.chat("b1", "第三章发生了什么？", chapter_number=3, mode="window")

    assert result["answer"] == "一次性回答"
    assert result["tokens_used"] == 10
    assert result["context_mode"] == "window"
    # The fake session returns every chapter regardless of the bounds clause,
    # so only assert that the reader's chapter made it into the context.
    assert 3 in [s["chapter_number"] for s in result["sources"]]

    sent = RECEIVED[0]["payload"]["messages"]
    assert sent[0]["role"] == "system"
    prompt = sent[-1]["content"]
    assert "测试小说" in prompt
    assert "这是第3章的正文内容" in prompt
    assert "第三章发生了什么？" in prompt
