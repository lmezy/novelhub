"""AI provider configuration.

The AI feature used to be configurable only through ``AI_*`` environment
variables, which means changing a model or pasting a key required rebuilding
the backend container.  The configuration now lives in ``app_settings`` (like
auto-sync and registration approval) and is editable from the admin UI, with
the environment variables kept as defaults so existing deployments keep
working.

API keys are stored encrypted (AES-GCM via ``COOKIE_SECRET``), the same
treatment cookies get: the repository and the database dumps never carry a
plaintext credential.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loguru import logger

from app.models.app_setting import AppSetting
from app.services.cookie_crypto import decrypt_cookie, encrypt_cookie

# -- app_settings keys -------------------------------------------------------

AI_ENABLED_KEY = "ai_enabled"
AI_PROVIDER_KEY = "ai_provider"
AI_BASE_URL_KEY = "ai_base_url"
AI_API_KEY_KEY = "ai_api_key"
AI_MODEL_KEY = "ai_model"
AI_TEMPERATURE_KEY = "ai_temperature"
AI_MAX_TOKENS_KEY = "ai_max_tokens"
AI_TIMEOUT_KEY = "ai_timeout"
AI_USE_PROXY_KEY = "ai_use_proxy"
AI_PROXY_URL_KEY = "ai_proxy_url"
AI_CONTEXT_CHARS_KEY = "ai_context_chars"
AI_RAG_ENABLED_KEY = "ai_rag_enabled"
AI_RAG_TOP_K_KEY = "ai_rag_top_k"
AI_EMBEDDING_PROVIDER_KEY = "ai_embedding_provider"
AI_EMBEDDING_BASE_URL_KEY = "ai_embedding_base_url"
AI_EMBEDDING_API_KEY_KEY = "ai_embedding_api_key"
AI_EMBEDDING_MODEL_KEY = "ai_embedding_model"

ALL_KEYS = (
    AI_ENABLED_KEY,
    AI_PROVIDER_KEY,
    AI_BASE_URL_KEY,
    AI_API_KEY_KEY,
    AI_MODEL_KEY,
    AI_TEMPERATURE_KEY,
    AI_MAX_TOKENS_KEY,
    AI_TIMEOUT_KEY,
    AI_USE_PROXY_KEY,
    AI_PROXY_URL_KEY,
    AI_CONTEXT_CHARS_KEY,
    AI_RAG_ENABLED_KEY,
    AI_RAG_TOP_K_KEY,
    AI_EMBEDDING_PROVIDER_KEY,
    AI_EMBEDDING_BASE_URL_KEY,
    AI_EMBEDDING_API_KEY_KEY,
    AI_EMBEDDING_MODEL_KEY,
)

#: ``AIConfig`` field name -> stored setting key.  Callers (the admin route, the
#: UI) speak in field names, storage speaks in ``ai_*`` keys.
FIELD_TO_KEY = {
    "enabled": AI_ENABLED_KEY,
    "provider": AI_PROVIDER_KEY,
    "base_url": AI_BASE_URL_KEY,
    "api_key": AI_API_KEY_KEY,
    "model": AI_MODEL_KEY,
    "temperature": AI_TEMPERATURE_KEY,
    "max_tokens": AI_MAX_TOKENS_KEY,
    "timeout": AI_TIMEOUT_KEY,
    "use_proxy": AI_USE_PROXY_KEY,
    "proxy_url": AI_PROXY_URL_KEY,
    "context_chars": AI_CONTEXT_CHARS_KEY,
    "rag_enabled": AI_RAG_ENABLED_KEY,
    "rag_top_k": AI_RAG_TOP_K_KEY,
    "embedding_provider": AI_EMBEDDING_PROVIDER_KEY,
    "embedding_base_url": AI_EMBEDDING_BASE_URL_KEY,
    "embedding_api_key": AI_EMBEDDING_API_KEY_KEY,
    "embedding_model": AI_EMBEDDING_MODEL_KEY,
}

# -- provider presets --------------------------------------------------------

# ``kind`` selects the wire protocol: OpenAI-compatible ``/chat/completions``
# (which every Chinese vendor and every local runtime speaks) or Anthropic's
# ``/v1/messages``.  ``needs_key`` marks providers that legitimately run
# without a credential (a local Ollama), so "no key" is not reported as a
# configuration error for them.
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
    },
    "deepseek": {
        "label": "DeepSeek",
        "kind": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "embedding_model": "",
    },
    "qwen": {
        "label": "通义千问 / DashScope",
        "kind": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "embedding_model": "text-embedding-v3",
    },
    "moonshot": {
        "label": "Kimi / Moonshot",
        "kind": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "embedding_model": "",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "kind": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "embedding_model": "embedding-3",
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "kind": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "embedding_model": "BAAI/bge-m3",
    },
    "ollama": {
        "label": "Ollama（本地）",
        "kind": "openai",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
        "embedding_model": "nomic-embed-text",
        "needs_key": False,
    },
    "claude": {
        "label": "Anthropic Claude",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-3-5-haiku-latest",
        "embedding_model": "",
    },
    "hermes": {
        "label": "Hermes（本地）",
        "kind": "openai",
        "base_url": "http://localhost:8080/v1",
        "model": "hermes-3",
        "embedding_model": "",
        "needs_key": False,
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "kind": "openai",
        "base_url": "",
        "model": "",
        "embedding_model": "",
    },
}

DEFAULT_PROVIDER = "openai"

#: Embedding defaults for providers that can serve vectors but are picked as an
#: embedding provider independently of the chat provider.
EMBEDDING_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "text-embedding-3-small",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "text-embedding-v3",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "embedding-3",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "BAAI/bge-m3",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "nomic-embed-text",
    },
}

#: Providers known to expose an OpenAI-compatible ``/embeddings`` endpoint.
EMBEDDING_CAPABLE = ("openai", "qwen", "zhipu", "siliconflow", "ollama", "custom")

MASKED_KEY = "********"


def normalize_provider(name: str | None) -> str:
    value = str(name or "").strip().lower()
    return value if value in PROVIDER_PRESETS else DEFAULT_PROVIDER


def provider_kind(provider: str | None) -> str:
    preset = PROVIDER_PRESETS.get(normalize_provider(provider), {})
    return str(preset.get("kind") or "openai")


def _preset(provider: str | None) -> dict[str, Any]:
    return PROVIDER_PRESETS.get(normalize_provider(provider), {})


def _embedding_preset(provider: str | None) -> dict[str, str]:
    key = normalize_provider(provider)
    if key in EMBEDDING_PRESETS:
        return EMBEDDING_PRESETS[key]
    # Fall back to the chat preset's embedding hint (may be empty).
    preset = _preset(key)
    return {
        "base_url": str(preset.get("base_url") or ""),
        "model": str(preset.get("embedding_model") or ""),
    }


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return "enc:" + encrypt_cookie(value)


def decrypt_secret(value: str) -> str:
    """Decrypt a stored secret, tolerating legacy plaintext values.

    A stored value that cannot be decrypted (for example after ``COOKIE_SECRET``
    was rotated) resolves to an empty key: the admin UI then reports "API Key
    empty" instead of sending a base64 blob upstream and surfacing a 401.
    """
    text = str(value or "")
    if not text:
        return ""
    if text.startswith("enc:"):
        try:
            return decrypt_cookie(text[4:])
        except Exception as exc:
            logger.warning(
                "Stored AI credential could not be decrypted ({}); "
                "re-enter it in the admin AI settings.", type(exc).__name__,
            )
            return ""
    return text


@dataclass(frozen=True)
class AIConfig:
    """Resolved AI configuration (chat + embeddings)."""

    enabled: bool = False
    provider: str = DEFAULT_PROVIDER
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout: float = 90.0
    use_proxy: bool = False
    proxy_url: str = ""
    context_chars: int = 24000
    rag_enabled: bool = True
    rag_top_k: int = 6
    embedding_provider: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""

    # -- derived ------------------------------------------------------------

    @property
    def kind(self) -> str:
        return provider_kind(self.provider)

    @property
    def effective_base_url(self) -> str:
        return (self.base_url or str(_preset(self.provider).get("base_url") or "")).rstrip("/")

    @property
    def effective_model(self) -> str:
        return self.model or str(_preset(self.provider).get("model") or "")

    @property
    def provider_label(self) -> str:
        return str(_preset(self.provider).get("label") or self.provider)

    @property
    def needs_key(self) -> bool:
        return bool(_preset(self.provider).get("needs_key", True))

    @property
    def effective_embedding_provider(self) -> str:
        return normalize_provider(self.embedding_provider or self.provider)

    @property
    def effective_embedding_base_url(self) -> str:
        if self.embedding_base_url:
            return self.embedding_base_url.rstrip("/")
        if self.embedding_provider and self.embedding_provider != self.provider:
            return str(_embedding_preset(self.embedding_provider).get("base_url") or "").rstrip("/")
        return self.effective_base_url

    @property
    def effective_embedding_model(self) -> str:
        if self.embedding_model:
            return self.embedding_model
        return str(_embedding_preset(self.effective_embedding_provider).get("model") or "")

    @property
    def effective_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.api_key

    @property
    def embeddings_supported(self) -> bool:
        return bool(
            self.effective_embedding_base_url
            and self.effective_embedding_model
            and self.effective_embedding_provider in EMBEDDING_CAPABLE
        )

    @property
    def configured(self) -> bool:
        """Whether a chat request has everything it needs to be attempted."""
        if not self.enabled:
            return False
        if not self.effective_base_url or not self.effective_model:
            return False
        if self.needs_key and not self.api_key:
            return False
        return True

    def public_dict(self) -> dict[str, Any]:
        """Configuration for the admin UI: never includes the raw key."""
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "provider": self.provider,
            "provider_label": self.provider_label,
            "provider_kind": self.kind,
            "base_url": self.base_url,
            "effective_base_url": self.effective_base_url,
            "model": self.model,
            "effective_model": self.effective_model,
            "api_key_set": bool(self.api_key),
            "api_key_hint": _key_hint(self.api_key),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "use_proxy": self.use_proxy,
            "proxy_url": self.proxy_url,
            "context_chars": self.context_chars,
            "rag_enabled": self.rag_enabled,
            "rag_top_k": self.rag_top_k,
            "embedding_provider": self.embedding_provider,
            "embedding_base_url": self.embedding_base_url,
            "embedding_model": self.embedding_model,
            "effective_embedding_model": self.effective_embedding_model,
            "effective_embedding_base_url": self.effective_embedding_base_url,
            "embedding_api_key_set": bool(self.embedding_api_key),
            "embeddings_supported": self.embeddings_supported,
            "providers": [
                {
                    "value": name,
                    "label": str(preset.get("label") or name),
                    "kind": preset.get("kind", "openai"),
                    "base_url": preset.get("base_url", ""),
                    "model": preset.get("model", ""),
                    "embedding_model": preset.get("embedding_model", ""),
                    "needs_key": bool(preset.get("needs_key", True)),
                    "embeddings": name in EMBEDDING_CAPABLE,
                }
                for name, preset in PROVIDER_PRESETS.items()
            ],
        }


def _key_hint(api_key: str) -> str:
    """A short, non-reversible hint so the admin can tell which key is stored."""
    key = str(api_key or "")
    if not key:
        return ""
    if len(key) <= 8:
        return MASKED_KEY
    return f"{key[:3]}{MASKED_KEY}{key[-4:]}"


def resolve_ai_config(
    stored: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> AIConfig:
    """Build an :class:`AIConfig` from stored settings plus env defaults.

    ``stored`` wins over ``env`` for every field, so an admin can override a
    container-level default without a rebuild while an un-configured install
    still picks up ``AI_API_KEY`` etc.
    """
    stored = {k: v for k, v in (stored or {}).items() if v is not None}
    env = env if env is not None else os.environ

    def pick(key: str, env_name: str, default: str = "") -> str:
        if key in stored and str(stored[key]).strip() != "":
            return str(stored[key]).strip()
        value = env.get(env_name, "")
        return str(value).strip() if value else default

    provider = normalize_provider(pick(AI_PROVIDER_KEY, "AI_PROVIDER", DEFAULT_PROVIDER))
    preset = _preset(provider)

    api_key = pick(AI_API_KEY_KEY, "AI_API_KEY")
    if api_key:
        api_key = decrypt_secret(api_key)

    base_url = pick(AI_BASE_URL_KEY, "AI_BASE_URL", str(preset.get("base_url") or ""))
    model = pick(AI_MODEL_KEY, "AI_MODEL", str(preset.get("model") or ""))

    embedding_provider_raw = pick(AI_EMBEDDING_PROVIDER_KEY, "AI_EMBEDDING_PROVIDER")
    embedding_provider = (
        normalize_provider(embedding_provider_raw) if embedding_provider_raw else ""
    )
    embedding_api_key = pick(AI_EMBEDDING_API_KEY_KEY, "AI_EMBEDDING_API_KEY")
    if embedding_api_key:
        embedding_api_key = decrypt_secret(embedding_api_key)

    enabled_raw = pick(AI_ENABLED_KEY, "AI_ENABLED")
    if enabled_raw == "":
        # Nothing explicit: an install with a key (or a keyless local runtime)
        # is considered on, everything else off.
        enabled = bool(api_key) or not bool(preset.get("needs_key", True))
    else:
        enabled = _truthy(enabled_raw)

    return AIConfig(
        enabled=enabled,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=_as_float(pick(AI_TEMPERATURE_KEY, "AI_TEMPERATURE", "0.7"), 0.7),
        max_tokens=_as_int(pick(AI_MAX_TOKENS_KEY, "AI_MAX_TOKENS", "2048"), 2048),
        timeout=_as_float(pick(AI_TIMEOUT_KEY, "AI_TIMEOUT", "90"), 90.0),
        use_proxy=_truthy(pick(AI_USE_PROXY_KEY, "AI_USE_PROXY")),
        proxy_url=pick(AI_PROXY_URL_KEY, "AI_PROXY_URL"),
        context_chars=_as_int(pick(AI_CONTEXT_CHARS_KEY, "AI_CONTEXT_CHARS", "24000"), 24000),
        rag_enabled=_truthy(pick(AI_RAG_ENABLED_KEY, "AI_RAG_ENABLED"), True),
        rag_top_k=_as_int(pick(AI_RAG_TOP_K_KEY, "AI_RAG_TOP_K", "6"), 6),
        embedding_provider=embedding_provider,
        embedding_base_url=pick(AI_EMBEDDING_BASE_URL_KEY, "AI_EMBEDDING_BASE_URL"),
        embedding_api_key=embedding_api_key,
        embedding_model=pick(AI_EMBEDDING_MODEL_KEY, "AI_EMBEDDING_MODEL"),
    )


async def _load_raw(db: AsyncSession) -> dict[str, str]:
    rows = await db.scalars(select(AppSetting).where(AppSetting.key.in_(ALL_KEYS)))
    return {row.key: row.value for row in rows}


async def get_ai_config(db: AsyncSession) -> AIConfig:
    """Load the AI configuration from the database."""
    return resolve_ai_config(await _load_raw(db))


async def set_ai_config(db: AsyncSession, values: Mapping[str, Any]) -> AIConfig:
    """Persist a partial configuration update and return the resolved result.

    Accepts either ``AIConfig`` field names (``api_key``) or storage keys
    (``ai_api_key``); ``None`` means "not sent" and an empty string clears the
    field so it falls back to the provider preset / environment default.
    """
    existing = await db.scalars(select(AppSetting).where(AppSetting.key.in_(ALL_KEYS)))
    rows = {row.key: row for row in existing}

    prepared: dict[str, str] = {}
    for field, value in values.items():
        if value is None:
            continue
        key = FIELD_TO_KEY.get(field)
        if key is None:
            key = field if field in ALL_KEYS else None
        if key is None:
            continue
        if key in (AI_API_KEY_KEY, AI_EMBEDDING_API_KEY_KEY):
            text = str(value)
            # The UI sends the mask back when the admin did not retype the key.
            if text == MASKED_KEY:
                continue
            prepared[key] = encrypt_secret(text) if text else ""
        elif isinstance(value, bool):
            prepared[key] = "true" if value else "false"
        else:
            prepared[key] = str(value)

    for key, value in prepared.items():
        row = rows.get(key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    await db.commit()

    return await get_ai_config(db)
