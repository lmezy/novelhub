"""LLM HTTP client (OpenAI-compatible + Anthropic) with proxy support.

Kept separate from :mod:`app.services.ai` so the transport concerns -- base URL
normalisation, auth headers, retries, streaming, error translation -- can be
tested without a database or a book.

Two wire protocols are supported:

* ``openai``    -- ``POST {base}/chat/completions`` (OpenAI, DeepSeek, Qwen /
  DashScope, Kimi, GLM, SiliconFlow, Ollama, vLLM, LM Studio, ...);
* ``anthropic`` -- ``POST {base}/v1/messages`` (Claude, which is *not*
  OpenAI-compatible; the previous implementation posted the OpenAI payload to
  the Anthropic base URL, so the ``claude`` provider could never work).

Every request can be routed through the same proxy the crawler uses
(``proxy_config.json``), because the deployments this project targets reach
OpenAI/Anthropic only through a local clash/mihomo instance.
"""

from __future__ import annotations

import asyncio
import json
import ssl
import time
from typing import Any, AsyncIterator, Iterable, Sequence

import httpx
from loguru import logger

from app.services.ai_config import AIConfig

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = (1.0, 3.0)

_ssl_context: ssl.SSLContext | None = None


def default_ssl_context() -> ssl.SSLContext:
    """A cached TLS context built from httpx's own CA bundle.

    ``httpx.AsyncClient()`` parses the whole certifi bundle on construction.
    That costs ~2.5s on Windows (and a measurable amount everywhere else) and
    this module builds a client per AI request, so the parsed context is
    reused.  Trust behaviour is unchanged: the bundle is certifi, exactly what
    httpx picks by default.
    """
    global _ssl_context
    if _ssl_context is None:
        try:
            import certifi

            _ssl_context = ssl.create_default_context(cafile=certifi.where())
        except Exception:  # pragma: no cover - certifi always ships with httpx
            _ssl_context = ssl.create_default_context()
    return _ssl_context


class AIError(RuntimeError):
    """A user-facing AI failure (already translated into an actionable hint)."""

    def __init__(self, message: str, *, status: int | None = None,
                 provider: str = "", detail: str = ""):
        super().__init__(message)
        self.status = status
        self.provider = provider
        self.detail = detail


class LLMResult:
    def __init__(self, text: str, *, model: str = "", prompt_tokens: int = 0,
                 completion_tokens: int = 0, finish_reason: str = ""):
        self.text = text
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.finish_reason = finish_reason

    @property
    def tokens_used(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def resolve_proxy_url(cfg: AIConfig) -> str:
    """Proxy to use for this request ('' means direct)."""
    if not cfg.use_proxy:
        return ""
    if cfg.proxy_url:
        return cfg.proxy_url.strip()
    try:
        from app.services.proxy_config import get_proxy_config

        proxy = get_proxy_config()
        if proxy.enabled:
            return (proxy.https_proxy or proxy.http_proxy or "").strip()
    except Exception:  # pragma: no cover - defensive
        return ""
    return ""


def _endpoint(base_url: str, kind: str) -> str:
    """Normalise a user-supplied base URL into a concrete endpoint."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise AIError("AI 服务地址（Base URL）未配置。", provider=kind)
    lowered = base.lower()
    if kind == "anthropic":
        if lowered.endswith("/messages"):
            return base
        if lowered.endswith("/v1"):
            return base + "/messages"
        return base + "/v1/messages"
    if lowered.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _embeddings_endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise AIError("Embedding 服务地址未配置。")
    if base.lower().endswith("/embeddings"):
        return base
    return base + "/embeddings"


def _headers(cfg: AIConfig, *, api_key: str = "") -> dict[str, str]:
    key = api_key or cfg.api_key
    if cfg.kind == "anthropic":
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if key:
            headers["x-api-key"] = key
        return headers
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _split_system(messages: Sequence[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Anthropic takes the system prompt as a top-level field."""
    system_parts: list[str] = []
    rest: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system":
            system_parts.append(content)
            continue
        rest.append({"role": "assistant" if role == "assistant" else "user",
                     "content": content})
    if not rest:
        rest = [{"role": "user", "content": " "}]
    # Anthropic rejects two consecutive messages with the same role.
    merged: list[dict[str, str]] = []
    for message in rest:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["content"] += "\n\n" + message["content"]
        else:
            merged.append(dict(message))
    return "\n\n".join(system_parts), merged


def build_payload(cfg: AIConfig, messages: Sequence[dict[str, str]], *,
                  max_tokens: int | None = None, temperature: float | None = None,
                  stream: bool = False) -> tuple[str, dict[str, Any]]:
    """Return ``(url, payload)`` for the configured provider."""
    max_tokens = int(max_tokens or cfg.max_tokens)
    temperature = cfg.temperature if temperature is None else temperature
    if cfg.kind == "anthropic":
        system, chat = _split_system(messages)
        payload: dict[str, Any] = {
            "model": cfg.effective_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat,
        }
        if system:
            payload["system"] = system
        if stream:
            payload["stream"] = True
        return _endpoint(cfg.effective_base_url, "anthropic"), payload

    payload = {
        "model": cfg.effective_model,
        "messages": [
            {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
            for m in messages
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if stream:
        payload["stream"] = True
    return _endpoint(cfg.effective_base_url, "openai"), payload


def _error_hint(status: int, body: str, cfg: AIConfig) -> str:
    snippet = (body or "").strip().replace("\n", " ")[:300]
    where = cfg.effective_base_url or "AI 服务"
    if status in (401, 403):
        base = f"AI 服务拒绝了这个 API Key（HTTP {status}）。请检查 Key 是否正确、是否有该模型的权限。"
    elif status == 404:
        base = (f"AI 服务返回 404：接口路径或模型名不存在。请检查 Base URL（当前 {where}）"
                f"与模型名（当前 {cfg.effective_model}）。")
    elif status == 429:
        base = "AI 服务限流（HTTP 429）：请求过于频繁或余额/配额不足，稍后重试。"
    elif status == 400:
        base = "AI 服务拒绝了请求（HTTP 400）：通常是模型名不被支持或参数超限。"
    elif 500 <= status < 600:
        base = f"AI 服务上游错误（HTTP {status}），通常是服务端临时故障，可稍后重试。"
    else:
        base = f"AI 服务返回 HTTP {status}。"
    if snippet:
        base += f" 服务端信息：{snippet}"
    return base


def _transport_hint(exc: Exception, cfg: AIConfig, proxy_url: str) -> str:
    name = type(exc).__name__
    detail = str(exc) or name
    if isinstance(exc, httpx.TimeoutException):
        base = f"调用 AI 服务超时（{cfg.timeout:g}s，{name}）。"
    elif isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        base = f"无法连接 AI 服务（{name}）。"
    else:
        base = f"调用 AI 服务失败（{name}）。"
    if proxy_url:
        base += f" 当前走代理 {proxy_url}，如果代理不可用请在「设置 → AI」里关掉代理或换一个地址。"
    elif cfg.effective_base_url.startswith("https://api.openai.com") or \
            cfg.effective_base_url.startswith("https://api.anthropic.com"):
        base += " 该服务在部分网络下需要代理：请打开「设置 → AI → 使用代理」并填写 mihomo 的地址。"
    else:
        base += f" 目标地址：{cfg.effective_base_url or '(未配置)'}。"
    return base + f"（{detail[:200]}）"


class LLMClient:
    """Chat + embedding calls for one resolved configuration."""

    def __init__(self, cfg: AIConfig):
        self.cfg = cfg
        self.proxy_url = resolve_proxy_url(cfg)

    # -- transport ----------------------------------------------------------

    def _client(self, *, timeout: float | None = None) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout or self.cfg.timeout, connect=20.0),
            "follow_redirects": True,
            "verify": default_ssl_context(),
        }
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.AsyncClient(**kwargs)

    @staticmethod
    def _snippet(response: httpx.Response) -> str:
        try:
            return response.text[:400]
        except Exception:  # pragma: no cover - defensive
            return ""

    # -- chat ---------------------------------------------------------------

    async def chat(self, messages: Sequence[dict[str, str]], *,
                   max_tokens: int | None = None,
                   temperature: float | None = None,
                   retries: int = DEFAULT_MAX_RETRIES) -> LLMResult:
        url, payload = build_payload(
            self.cfg, messages, max_tokens=max_tokens, temperature=temperature
        )
        headers = _headers(self.cfg)
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                async with self._client() as client:
                    response = await client.post(url, headers=headers, json=payload)
                    if response.status_code >= 400:
                        if response.status_code in (429,) or response.status_code >= 500:
                            last_error = AIError(
                                _error_hint(response.status_code, self._snippet(response), self.cfg),
                                status=response.status_code,
                                provider=self.cfg.provider,
                            )
                            if attempt < retries:
                                await asyncio.sleep(RETRY_BACKOFF_SECONDS[
                                    min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                                continue
                            raise last_error
                        raise AIError(
                            _error_hint(response.status_code, self._snippet(response), self.cfg),
                            status=response.status_code,
                            provider=self.cfg.provider,
                        )
                    data = response.json()
                return self._parse_completion(data, payload)
            except AIError:
                raise
            except Exception as exc:  # transport
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[
                        min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise AIError(
                    _transport_hint(exc, self.cfg, self.proxy_url),
                    provider=self.cfg.provider,
                    detail=str(exc) or type(exc).__name__,
                ) from exc

        raise AIError(f"调用 AI 服务失败：{last_error}", provider=self.cfg.provider)

    def _parse_completion(self, data: dict[str, Any], payload: dict[str, Any]) -> LLMResult:
        if self.cfg.kind == "anthropic":
            blocks = data.get("content") or []
            text = "".join(
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
            usage = data.get("usage") or {}
            return LLMResult(
                text.strip(),
                model=str(data.get("model") or payload.get("model") or ""),
                prompt_tokens=int(usage.get("input_tokens") or 0),
                completion_tokens=int(usage.get("output_tokens") or 0),
                finish_reason=str(data.get("stop_reason") or ""),
            )

        choices = data.get("choices") or [{}]
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        text = message.get("content")
        if text is None:
            # Some providers return ``reasoning_content`` only, or a list of
            # content parts (newer OpenAI multimodal responses).
            text = message.get("reasoning_content") or ""
        if isinstance(text, list):
            text = "".join(
                str(part.get("text") or "")
                for part in text
                if isinstance(part, dict)
            )
        usage = data.get("usage") or {}
        return LLMResult(
            str(text or "").strip(),
            model=str(data.get("model") or payload.get("model") or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=str(choice.get("finish_reason") or ""),
        )

    async def stream(self, messages: Sequence[dict[str, str]], *,
                     max_tokens: int | None = None,
                     temperature: float | None = None,
                     retries: int = DEFAULT_MAX_RETRIES) -> AsyncIterator[str]:
        """Yield assistant text deltas. Retries only before the first token."""
        url, payload = build_payload(
            self.cfg, messages, max_tokens=max_tokens,
            temperature=temperature, stream=True,
        )
        headers = _headers(self.cfg)

        for attempt in range(retries + 1):
            emitted = False
            try:
                async with self._client() as client:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code >= 400:
                            body = (await response.aread()).decode("utf-8", "replace")
                            raise AIError(
                                _error_hint(response.status_code, body, self.cfg),
                                status=response.status_code,
                                provider=self.cfg.provider,
                            )
                        async for delta in self._iter_deltas(response):
                            emitted = True
                            yield delta
                return
            except AIError as exc:
                retryable = exc.status in (429,) or (exc.status or 0) >= 500
                if retryable and not emitted and attempt < retries:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[
                        min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise
            except Exception as exc:
                if not emitted and attempt < retries:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[
                        min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise AIError(
                    _transport_hint(exc, self.cfg, self.proxy_url),
                    provider=self.cfg.provider,
                    detail=str(exc) or type(exc).__name__,
                ) from exc

    async def _iter_deltas(self, response: httpx.Response) -> AsyncIterator[str]:
        async for line in response.aiter_lines():
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                if data == "[DONE]":
                    return
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            text = self._delta_text(payload)
            if text:
                yield text

    def _delta_text(self, payload: dict[str, Any]) -> str:
        if self.cfg.kind == "anthropic":
            if payload.get("type") == "content_block_delta":
                delta = payload.get("delta") or {}
                if delta.get("type") in (None, "text_delta"):
                    return str(delta.get("text") or "")
            return ""
        choices = payload.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content is None:
            content = delta.get("reasoning_content")
        if isinstance(content, list):
            return "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            )
        return str(content or "")

    # -- embeddings ---------------------------------------------------------

    async def embed(self, texts: Sequence[str], *, batch_size: int = 16,
                    retries: int = DEFAULT_MAX_RETRIES) -> list[list[float]]:
        if not texts:
            return []
        if not self.cfg.embeddings_supported:
            raise AIError(
                "当前配置不支持向量化（Embedding）：请在「设置 → AI」里指定一个支持 "
                "/embeddings 的服务（OpenAI / 通义千问 / 智谱 / SiliconFlow / Ollama）"
                "与向量模型。"
            )

        url = _embeddings_endpoint(self.cfg.effective_embedding_base_url)
        headers = _headers(self.cfg, api_key=self.cfg.effective_embedding_api_key)
        vectors: list[list[float]] = []

        for start in range(0, len(texts), batch_size):
            batch = list(texts[start:start + batch_size])
            payload = {"model": self.cfg.effective_embedding_model, "input": batch}
            # DashScope's compatible mode rejects ``input`` arrays longer than
            # its own limit and wants ``texts`` for the native endpoint; the
            # OpenAI-compatible ``input`` form is what every other provider
            # accepts, so keep it and let the batch size bound the request.
            for attempt in range(retries + 1):
                try:
                    async with self._client(timeout=max(self.cfg.timeout, 120.0)) as client:
                        response = await client.post(url, headers=headers, json=payload)
                        if response.status_code >= 400:
                            retryable = response.status_code == 429 or response.status_code >= 500
                            error = AIError(
                                _error_hint(response.status_code, self._snippet(response), self.cfg),
                                status=response.status_code,
                                provider=self.cfg.effective_embedding_provider,
                            )
                            if retryable and attempt < retries:
                                await asyncio.sleep(RETRY_BACKOFF_SECONDS[
                                    min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                                continue
                            raise error
                        data = response.json()
                    break
                except AIError:
                    raise
                except Exception as exc:
                    if attempt < retries:
                        await asyncio.sleep(RETRY_BACKOFF_SECONDS[
                            min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                        continue
                    raise AIError(
                        _transport_hint(exc, self.cfg, self.proxy_url),
                        provider=self.cfg.effective_embedding_provider,
                        detail=str(exc) or type(exc).__name__,
                    ) from exc

            items = data.get("data") or []
            if len(items) != len(batch):
                raise AIError(
                    f"向量化返回数量不匹配（请求 {len(batch)} 条，返回 {len(items)} 条）。"
                )
            for item in sorted(items, key=lambda i: int(i.get("index") or 0)):
                vector = item.get("embedding")
                if not isinstance(vector, list) or not vector:
                    raise AIError("向量化返回了空的 embedding。")
                vectors.append([float(x) for x in vector])

        return vectors

    # -- diagnostics --------------------------------------------------------

    async def test_connection(self) -> dict[str, Any]:
        """Round-trip a tiny completion so the admin gets a yes/no + reason."""
        started = time.monotonic()
        result = await self.chat(
            [
                {"role": "system", "content": "You are a connectivity probe. Reply with OK only."},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=16,
            temperature=0.0,
            retries=0,
        )
        elapsed = time.monotonic() - started
        return {
            "ok": True,
            "provider": self.cfg.provider,
            "kind": self.cfg.kind,
            "base_url": self.cfg.effective_base_url,
            "endpoint": _endpoint(self.cfg.effective_base_url, self.cfg.kind),
            "model": result.model or self.cfg.effective_model,
            "latency_ms": int(elapsed * 1000),
            "reply": result.text[:200],
            "tokens_used": result.tokens_used,
            "proxy": self.proxy_url,
        }

    async def test_embeddings(self) -> dict[str, Any]:
        started = time.monotonic()
        vectors = await self.embed(["连通性测试"], retries=0)
        elapsed = time.monotonic() - started
        dimension = len(vectors[0]) if vectors else 0
        return {
            "ok": True,
            "provider": self.cfg.effective_embedding_provider,
            "base_url": self.cfg.effective_embedding_base_url,
            "model": self.cfg.effective_embedding_model,
            "dimension": dimension,
            "latency_ms": int(elapsed * 1000),
            "proxy": self.proxy_url,
        }


def describe_model_chain(models: Iterable[str]) -> str:
    return " -> ".join(str(m) for m in models if m)


__all__ = [
    "AIError",
    "LLMClient",
    "LLMResult",
    "build_payload",
    "resolve_proxy_url",
]
