import { defineStore } from "pinia"
import { computed, ref } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "./i18n"

// ---------------------------------------------------------------------------
// AI provider settings + RAG index maintenance
// ---------------------------------------------------------------------------
const EMBEDDING_HINT_RE = /embed|bge|gte|m3|rerank/i

// ---------------------------------------------------------------------------
// Model providers
//
// The backend keeps a list of saved providers (endpoint + key + model) and one
// of them is in use.  The form edits a working copy of that list, so switching
// vendors is a click instead of retyping a key.  A key is never echoed back: an
// entry only reports whether it has one, plus a masked hint.
// ---------------------------------------------------------------------------
export interface AIProviderDraft {
  id: string
  name: string
  provider: string
  base_url: string
  model: string
  provider_label?: string
  effective_base_url?: string
  effective_model?: string
  api_key_set?: boolean
  api_key_hint: string
  configured?: boolean
  //: Typed into the form but not saved yet; sent on save, then cleared.
  api_key_input?: string
  //: The admin asked to drop the stored key of this entry.
  api_key_clear?: boolean
  //: A local draft the server has not stored yet.
  draft?: boolean
}

function newProfileId(): string {
  const random = globalThis.crypto?.randomUUID?.()
  return random ? `p_${random}` : `p_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
}

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
  const aiEmbeddingKeyInput = ref("")
  const aiModels = ref<string[]>([])
  const aiModelsLoading = ref(false)
  const aiModelsError = ref("")
  const aiProfiles = ref<AIProviderDraft[]>([])
  const aiActiveProfileId = ref("")
  //: "profiles" once the server has a saved list, "single" while only the
  //: single-provider keys exist.
  const aiProfileSource = ref("single")

  const aiActiveProfile = computed(
    () =>
      aiProfiles.value.find((profile) => profile.id === aiActiveProfileId.value) ||
      aiProfiles.value[0] ||
      null,
  )

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

  function profileDraftFrom(value: any): AIProviderDraft {
    return {
      id: String(value?.id || newProfileId()),
      name: String(value?.name || ""),
      provider: String(value?.provider || "openai"),
      base_url: String(value?.base_url || ""),
      model: String(value?.model || ""),
      provider_label: value?.provider_label,
      effective_base_url: String(value?.effective_base_url || ""),
      effective_model: String(value?.effective_model || ""),
      api_key_set: !!value?.api_key_set,
      api_key_hint: String(value?.api_key_hint || ""),
      configured: value?.configured,
      api_key_input: "",
      api_key_clear: false,
    }
  }

  /** The admin form for one `/admin/ai` response.
   *
   * One mapping for both the initial load and every save response: the tuning
   * boxes (temperature / max_tokens / timeout / context_chars) used to be read
   * only on load, so a field the API dropped looked saved until the next reload
   * (reported 2026-10-20).  Rebuilding the form from the response makes the
   * screen show exactly what the server stored.
   */
  function formFromConfig(res: any) {
    return {
      enabled: res.enabled,
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
  }

  async function loadAIConfig() {
    aiLoading.value = true
    aiError.value = ""
    try {
      const res = await api.get<any>("/admin/ai")
      aiConfig.value = res
      aiForm.value = formFromConfig(res)
      aiProfileSource.value = res.profile_source === "profiles" ? "profiles" : "single"
      const saved = Array.isArray(res.profiles) ? res.profiles : []
      if (saved.length) {
        aiProfiles.value = saved.map(profileDraftFrom)
        aiActiveProfileId.value =
          saved.find((profile: any) => profile.active || profile.id === res.active_profile_id)?.id ||
          aiProfiles.value[0].id
      } else {
        // No list yet: show the current single configuration as one draft, so
        // saving turns it into the first provider without retyping anything.
        aiProfiles.value = [
          {
            id: newProfileId(),
            name: "",
            provider: res.provider || "openai",
            base_url: res.base_url || "",
            model: res.model || "",
            provider_label: res.provider_label,
            api_key_set: !!res.api_key_set,
            api_key_hint: res.api_key_hint || "",
            configured: res.configured,
            api_key_input: "",
            draft: true,
          },
        ]
        aiActiveProfileId.value = aiProfiles.value[0].id
      }
      aiEmbeddingKeyInput.value = ""
    } catch (e) {
      aiError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
    } finally {
      aiLoading.value = false
    }
  }

  function addAIProfile() {
    const draft = profileDraftFrom({
      provider: aiActiveProfile.value?.provider || "openai",
    })
    draft.draft = true
    aiProfiles.value = [...aiProfiles.value, draft]
    aiActiveProfileId.value = draft.id
  }

  function duplicateAIProfile(id: string) {
    const source = aiProfiles.value.find((profile) => profile.id === id)
    if (!source) return
    const copy: AIProviderDraft = {
      ...source,
      id: newProfileId(),
      name: source.name ? `${source.name} 副本` : "",
      api_key_input: "",
      api_key_clear: false,
      draft: true,
    }
    aiProfiles.value = [...aiProfiles.value, copy]
    aiActiveProfileId.value = copy.id
  }

  function removeAIProfile(id: string) {
    const index = aiProfiles.value.findIndex((profile) => profile.id === id)
    if (index < 0) return
    const draft = aiProfiles.value[index]
    const label = draft.name || draft.provider_label || draft.provider
    if (!window.confirm(i18n.t('admin_ai_profile_delete_confirm', { name: label }))) return
    const next = aiProfiles.value.filter((profile) => profile.id !== id)
    aiProfiles.value = next
    if (aiActiveProfileId.value === id) {
      aiActiveProfileId.value = next[0]?.id || ""
    }
  }

  function selectAIProfile(id: string) {
    if (aiActiveProfileId.value === id) return
    aiActiveProfileId.value = id
    // A key typed for the previous entry must not follow us to this one.
  }

  function clearAIProfileKey(id: string) {
    const profile = aiProfiles.value.find((item) => item.id === id)
    if (!profile) return
    profile.api_key_input = ""
    profile.api_key_clear = true
    profile.api_key_set = false
    profile.api_key_hint = ""
  }

  function applyProviderPreset(name: string) {
    const profile = aiActiveProfile.value
    const preset = (aiConfig.value?.providers || []).find((p: any) => p.value === name)
    if (!profile || !preset) return
    profile.provider = name
    profile.base_url = preset.base_url || ""
    profile.model = preset.model || ""
  }

  function profilePayload() {
    return aiProfiles.value.map((profile) => {
      const entry: Record<string, any> = {
        id: profile.id,
        name: profile.name,
        provider: profile.provider,
        base_url: profile.base_url,
        model: profile.model,
      }
      const typed = String(profile.api_key_input || "").trim()
      if (typed) entry.api_key = typed
      else if (profile.api_key_clear) entry.api_key = ""
      // Otherwise the field is omitted and this entry's stored key stays.
      return entry
    })
  }

  async function saveAIConfig() {
    aiSaving.value = true
    aiError.value = ""
    aiSaved.value = false
    try {
      // Never send a half-filled box: "" / NaN / undefined for a numeric field
      // made the whole PUT fail with a 422 (the other numbers then looked
      // unsaveable too).  Omitted keys mean "leave as is".
      const body: Record<string, any> = {}
      for (const [key, value] of Object.entries(aiForm.value as Record<string, any>)) {
        if (value === undefined || value === null) continue
        if (typeof value === "string" && !value.trim()) continue
        if (typeof value === "number" && !Number.isFinite(value)) continue
        body[key] = value
      }
      body.profiles = profilePayload()
      body.active_profile_id = aiActiveProfileId.value || aiProfiles.value[0]?.id || ""
      // Every provider key is in the list above; the single-provider field is
      // only what a profile inherits when it has no key of its own.
      if (aiEmbeddingKeyInput.value) body.embedding_api_key = aiEmbeddingKeyInput.value
      const res = await api.put<any>("/admin/ai", body)
      aiConfig.value = { ...aiConfig.value, ...res }
      // Show what the server actually stored, not what was typed: a dropped
      // field reverts here instead of looking saved until the next page load.
      aiForm.value = formFromConfig({ ...aiForm.value, ...res })
      aiProfileSource.value = res.profile_source === "profiles" ? "profiles" : "single"
      const saved = Array.isArray(res.profiles) ? res.profiles : []
      if (saved.length) {
        aiProfiles.value = saved.map(profileDraftFrom)
        aiActiveProfileId.value =
          saved.find((profile: any) => profile.active || profile.id === res.active_profile_id)?.id ||
          aiProfiles.value[0].id
      }
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
      const profile = aiActiveProfile.value
      const body: any = {
        provider: profile?.provider,
        base_url: profile?.base_url,
      }
      // Use the key the admin just typed, otherwise the stored one.
      const typed = String(profile?.api_key_input || "").trim()
      if (typed) body.api_key = typed
      const res = await api.post<any>("/admin/ai/models", body)
      aiModels.value = res.models || []
      if (!res.ok) {
        aiModelsError.value = res.error || i18n.t('admin_ai_models_failed')
        return
      }
      // Fill an empty field with a model the server just confirmed it accepts,
      // preferring a chat model for the chat field.
      const chat = res.models.find((m: string) => !EMBEDDING_HINT_RE.test(m))
      if (profile && !profile.model && chat) profile.model = chat
    } catch (e) {
      aiModelsError.value = e instanceof Error ? e.message : i18n.t('admin_ai_models_failed')
    } finally {
      aiModelsLoading.value = false
    }
  }

  function applyModel(target: "model" | "embedding_model", value: string) {
    const profile = aiActiveProfile.value
    if (target === "model" && profile) {
      profile.model = value
      return
    }
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
    aiEmbeddingKeyInput,
    aiModels,
    aiModelsLoading,
    aiModelsError,
    chatModelOptions,
    embeddingModelOptions,
    aiProfiles,
    aiActiveProfileId,
    aiActiveProfile,
    aiProfileSource,
    aiIndexBookId,
    aiIndexBusy,
    aiIndexError,
    aiIndexResult,
    aiIndexedBooks,
    aiIndexedLoading,
    loadAIConfig,
    addAIProfile,
    duplicateAIProfile,
    removeAIProfile,
    selectAIProfile,
    clearAIProfileKey,
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
