"""Admin AI settings: blank boxes, and providers without an embeddings API."""
import pytest
from unittest.mock import AsyncMock, patch

from app.api.routes.admin import AIConfigUpdate
from app.services.ai_client import diagnose
from app.services.ai_config import AIConfig


def test_blank_number_boxes_are_treated_as_not_sent():
    """An empty box must not fail the whole save (reported 2026-10-21)."""
    payload = AIConfigUpdate(temperature="", max_tokens="", timeout="",
                             context_chars="", rag_top_k="")

    assert payload.temperature is None
    assert payload.max_tokens is None
    assert payload.timeout is None
    assert payload.context_chars is None
    assert payload.rag_top_k is None


def test_real_numbers_still_validate():
    payload = AIConfigUpdate(temperature="0.7", max_tokens="2048",
                             timeout="90", context_chars="24000", rag_top_k="6")

    assert payload.temperature == 0.7
    assert payload.max_tokens == 2048
    assert payload.timeout == 90
    assert payload.context_chars == 24000
    assert payload.rag_top_k == 6


def test_chat_only_vendor_is_not_embeddings_capable():
    """Command Code answers /chat/completions but has no /embeddings route."""
    config = AIConfig(
        provider="custom",
        base_url="https://api.commandcode.ai/provider/v1",
        model="minimax/minimax-m3.1-flash:free",
        embedding_provider="custom",
        embedding_base_url="",
        embedding_model="minimax/minimax-m3.1-flash:free",
    )

    assert config.embeddings_supported is False

    # Naming the endpoint explicitly (a real embeddings service) makes it work.
    explicit = AIConfig(
        provider="custom",
        base_url="https://api.commandcode.ai/provider/v1",
        model="minimax/minimax-m3.1-flash:free",
        embedding_provider="custom",
        embedding_base_url="https://api.siliconflow.cn/v1",
        embedding_model="BAAI/bge-m3",
    )
    assert explicit.embeddings_supported is True


def test_vendors_that_document_embeddings_still_inherit_the_endpoint():
    config = AIConfig(
        provider="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
    )

    assert config.embeddings_supported is True


@pytest.mark.asyncio
async def test_diagnose_explains_a_missing_embeddings_api_instead_of_a_404():
    config = AIConfig(
        provider="custom",
        base_url="https://api.commandcode.ai/provider/v1",
        model="minimax/minimax-m3.1-flash:free",
        embedding_provider="custom",
        embedding_model="minimax/minimax-m3.1-flash:free",
    )

    with patch("app.services.ai_client.LLMClient.test_connection",
               AsyncMock(return_value={"ok": True})), \
         patch("app.services.ai_client.LLMClient.test_embeddings",
               AsyncMock(side_effect=AssertionError("must not be called"))):
        results = await diagnose(config, test_embeddings=True)

    assert results["embeddings"]["ok"] is False
    assert results["embeddings"]["supported"] is False
    assert "向量接口" in results["embeddings"]["error"]
