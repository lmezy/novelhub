import { defineStore } from "pinia"
import { computed, ref } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "./i18n"

// ---------------------------------------------------------------------------
// AI provider settings + RAG index maintenance
// ---------------------------------------------------------------------------
const EMBEDDING_HINT_RE = /embed|bge|gte|m3|rerank/i

export const useAdminAIStore = defineStore("adminAI", () => {
  const i18n = useI18nStore()

  const aiConfig = ref<any>(null)
  const aiForm = ref<Record<string, any>>({})
  const aiLoading = ref(false)
  const aiSaving = ref(false)
  const aiError = ref("")
  const aiSaved = ref(false)
  const aiTesting = ref(false)
  const aiTestResult = ref<any>(null)
  const aiApiKeyInput = ref("")
  const aiEmbeddingKeyInput = ref("")
  const aiModels = ref<string[]>([])
  const aiModelsLoading = ref(false)
  const aiModelsError = ref("")

  const chatModelOptions = computed(() =>
    aiModels.value.filter((m) => !EMBEDDING_HINT_RE.test(m)),
  )
  const embeddingModelOptions = computed(() => {
    const picked = aiModels.value.filter((m) => EMBEDDING_HINT_RE.test(m))
    return picked.length ? picked : aiModels.value
  })

  const aiIndexBookId = ref("")
  const aiIndexBusy = ref(false)
  const aiIndexError = ref("")
  const aiIndexResult = ref<any>(null)
  const aiIndexedBooks = ref<any[]>([])
  const aiIndexedLoading = ref(false)

  async function loadAIConfig() {
    aiLoading.value = true
    aiError.value = ""
    try {
      const res = await api.get<any>("/admin/ai")
      aiConfig.value = res
      aiForm.value = {
        enabled: res.enabled,
        provider: res.provider,
        base_url: res.base_url || "",
        model: res.model || "",
        temperature: res.temperature,
        max_tokens: res.max_tokens,
        timeout: res.timeout,
        use_proxy: res.use_proxy,
        proxy_url: res.proxy_url || "",
        context_chars: res.context_chars,
        rag_enabled: res.rag_enabled,
        rag_top_k: res.rag_top_k,
        embedding_provider: res.embedding_provider || "",
        embedding_base_url: res.embedding_base_url || "",
        embedding_model: res.embedding_model || "",
        auto_diagnose: res.auto_diagnose !== false,
      }
      aiApiKeyInput.value = ""
      aiEmbeddingKeyInput.value = ""
    } catch (e) {
      aiError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
    } finally {
      aiLoading.value = false
    }
  }

  function applyProviderPreset(name: string) {
    const preset = (aiConfig.value?.providers || []).find((p: any) => p.value === name)
    if (!preset) return
    aiForm.value.base_url = preset.base_url || ""
    aiForm.value.model = preset.model || ""
    aiForm.value.embedding_model = ""
  }

  async function saveAIConfig() {
    aiSaving.value = true
    aiError.value = ""
    aiSaved.value = false
    try {
      const body: any = { ...aiForm.value }
      if (aiApiKeyInput.value) body.api_key = aiApiKeyInput.value
      if (aiEmbeddingKeyInput.value) body.embedding_api_key = aiEmbeddingKeyInput.value
      const res = await api.put<any>("/admin/ai", body)
      aiConfig.value = { ...aiConfig.value, ...res }
      aiApiKeyInput.value = ""
      aiEmbeddingKeyInput.value = ""
      aiSaved.value = true
      window.setTimeout(() => (aiSaved.value = false), 2000)
    } catch (e) {
      aiError.value = e instanceof Error ? e.message : i18n.t('admin_ai_save_failed')
    } finally {
      aiSaving.value = false
    }
  }

  async function testAIConfig() {
    aiTesting.value = true
    aiTestResult.value = null
    aiError.value = ""
    try {
      aiTestResult.value = await api.post<any>("/admin/ai/test", {})
      if (aiTestResult.value?.available_models?.length) {
        aiModels.value = aiTestResult.value.available_models
      }
    } catch (e) {
      aiError.value = e instanceof Error ? e.message : i18n.t('admin_ai_test_failed')
    } finally {
      aiTesting.value = false
    }
  }

  async function loadAIModels() {
    aiModelsLoading.value = true
    aiModelsError.value = ""
    try {
      const body: any = {
        provider: aiForm.value.provider,
        base_url: aiForm.value.base_url,
      }
      // Use the key the admin just typed, otherwise the stored one.
      if (aiApiKeyInput.value) body.api_key = aiApiKeyInput.value
      const res = await api.post<any>("/admin/ai/models", body)
      aiModels.value = res.models || []
      if (!res.ok) {
        aiModelsError.value = res.error || i18n.t('admin_ai_models_failed')
        return
      }
      // Fill an empty field with a model the server just confirmed it accepts,
      // preferring a chat model for the chat field.
      const chat = res.models.find((m: string) => !EMBEDDING_HINT_RE.test(m))
      if (!aiForm.value.model && chat) aiForm.value.model = chat
    } catch (e) {
      aiModelsError.value = e instanceof Error ? e.message : i18n.t('admin_ai_models_failed')
    } finally {
      aiModelsLoading.value = false
    }
  }

  function applyModel(target: "model" | "embedding_model", value: string) {
    aiForm.value[target] = value
  }

  async function loadIndexedBooks() {
    aiIndexedLoading.value = true
    try {
      aiIndexedBooks.value = await api.get<any[]>("/rag/index?limit=50")
    } catch {
      aiIndexedBooks.value = []
    } finally {
      aiIndexedLoading.value = false
    }
  }

  async function indexBookForRAG() {
    const id = aiIndexBookId.value.trim()
    if (!id) return
    aiIndexBusy.value = true
    aiIndexError.value = ""
    aiIndexResult.value = null
    try {
      aiIndexResult.value = await api.post<any>("/rag/index/" + encodeURIComponent(id))
      await loadIndexedBooks()
    } catch (e) {
      aiIndexError.value = e instanceof Error ? e.message : i18n.t('admin_ai_index_failed')
    } finally {
      aiIndexBusy.value = false
    }
  }

  async function deleteRAGIndex(bookId: string) {
    if (!confirm(i18n.t('admin_ai_index_delete_confirm'))) return
    try {
      await api.delete("/rag/index/" + encodeURIComponent(bookId))
      await loadIndexedBooks()
    } catch (e) {
      aiIndexError.value = e instanceof Error ? e.message : i18n.t('admin_ai_index_failed')
    }
  }

  return {
    aiConfig,
    aiForm,
    aiLoading,
    aiSaving,
    aiError,
    aiSaved,
    aiTesting,
    aiTestResult,
    aiApiKeyInput,
    aiEmbeddingKeyInput,
    aiModels,
    aiModelsLoading,
    aiModelsError,
    chatModelOptions,
    embeddingModelOptions,
    aiIndexBookId,
    aiIndexBusy,
    aiIndexError,
    aiIndexResult,
    aiIndexedBooks,
    aiIndexedLoading,
    loadAIConfig,
    applyProviderPreset,
    saveAIConfig,
    testAIConfig,
    loadAIModels,
    applyModel,
    loadIndexedBooks,
    indexBookForRAG,
    deleteRAGIndex,
  }
})
