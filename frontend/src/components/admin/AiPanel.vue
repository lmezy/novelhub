<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue"
import { useI18nStore } from "../../stores/i18n"
import { useAdminAIStore } from "../../stores/adminAI"
import AiProviderList from "./AiProviderList.vue"

const i18n = useI18nStore()
const aiStore = useAdminAIStore()
const forceChapterIndex = ref(false)

// -- background RAG indexing (batch + "打开即建") --------------------------
//
// One server-side runner drains the queue, so the panel only polls.  The
// numbers below are what the admin needs before spending embedding quota:
// how many books this run will touch, roughly how much Postgres space that is,
// and how many embedding calls it costs (16 chunks per call).
const POLL_MS = 4000

const job = computed(() => aiStore.ragJobStatus)
const jobRunning = computed(() => !!job.value?.running)
const jobCancelling = computed(() => !!job.value?.cancel_requested)
const jobTotal = computed(() => Number(job.value?.total || 0))
const jobShowsProgress = computed(() => jobRunning.value || job.value?.phase === "done")
const jobProcessed = computed(
  () =>
    Number(job.value?.done || 0) +
    Number(job.value?.skipped || 0) +
    Number(job.value?.failed || 0),
)
const jobPercent = computed(() => {
  if (!jobTotal.value) return jobRunning.value ? 5 : 0
  return Math.max(0, Math.min(100, Math.round((jobProcessed.value / jobTotal.value) * 100)))
})
const jobPhaseLabel = computed(() => {
  const phase = String(job.value?.phase || "idle")
  if (phase === "done") return i18n.t("admin_ai_rag_job_done")
  if (phase === "cancelled") return i18n.t("admin_ai_rag_job_cancelled")
  if (phase === "failed") return i18n.t("admin_ai_rag_job_failed")
  if (phase === "interrupted") return i18n.t("admin_ai_rag_job_interrupted")
  if (jobRunning.value) return i18n.t("admin_ai_rag_job_running")
  return i18n.t("admin_ai_rag_job_idle")
})
const jobCurrent = computed(() => String(job.value?.current?.title || ""))
const jobLastError = computed(() => {
  if (job.value?.error) return String(job.value.error)
  const errors = Array.isArray(job.value?.recent_errors) ? job.value.recent_errors : []
  if (!errors.length) return ""
  return String(errors[errors.length - 1]?.error || "")
})
const batchEstimate = computed(() => {
  const { limit: books, max_chunks: chunks } = aiStore.ragBatchOptions
  if (!books || !chunks) return null
  const perChunk = Number(job.value?.limits?.chunk_bytes_estimate) || 20480
  return {
    size: Math.max(1, Math.round((books * chunks * perChunk) / (1024 * 1024))),
    calls: books * Math.ceil(chunks / 16),
  }
})
const indexedStats = computed(() => {
  const stats = job.value?.stats || {}
  return {
    books: Number(stats.indexed_books || 0),
    chunks: Number(stats.chunks || 0),
    mb: Math.round(Number(stats.db_bytes || 0) / (1024 * 1024)),
  }
})

// -- whole-library profiles (cross-book discovery) --------------------------
const library = computed(() => aiStore.libraryStatus)
const libraryRunning = computed(() => !!library.value?.running)
const libraryCancelling = computed(() => !!library.value?.cancel_requested)
const libraryTotal = computed(() => Number(library.value?.total || 0))
const libraryDone = computed(
  () =>
    Number(library.value?.done || 0) +
    Number(library.value?.skipped || 0) +
    Number(library.value?.failed || 0),
)
const libraryPercent = computed(() => {
  if (!libraryTotal.value) return libraryRunning.value ? 5 : 0
  return Math.max(0, Math.min(100, Math.round((libraryDone.value / libraryTotal.value) * 100)))
})
const libraryPhaseLabel = computed(() => {
  const phase = String(library.value?.phase || "idle")
  if (phase === "done") return i18n.t("admin_ai_library_done")
  if (phase === "cancelled") return i18n.t("admin_ai_library_cancelled")
  if (phase === "failed") return i18n.t("admin_ai_library_failed_state")
  if (libraryRunning.value) return i18n.t("admin_ai_library_running")
  return i18n.t("admin_ai_library_idle")
})
const libraryCurrent = computed(() => String(library.value?.current?.title || ""))
const libraryStats = computed(() => {
  const stats = library.value?.stats || {}
  return {
    books: Number(stats.books || 0),
    vectors: Number(stats.vectors || 0),
    mb: Math.round(Number(stats.db_bytes || 0) / (1024 * 1024)),
  }
})
const libraryEstimate = computed(() => {
  const books = aiStore.libraryBuildLimit
  if (!books) return null
  return {
    calls: Math.ceil(books / 4), // four books of four segments per request
    size: Math.max(1, Math.round((books * 4 * 7) / 1024)), // 7 KB per segment
  }
})

let pollTimer: number | undefined
onMounted(() => {
  void aiStore.loadRagJobStatus()
  void aiStore.loadLibraryStatus()
  pollTimer = window.setInterval(() => {
    void aiStore.loadRagJobStatus()
    void aiStore.loadLibraryStatus()
  }, POLL_MS)
})
onUnmounted(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
<section class="space-y-6">
  <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
    <div class="flex items-center justify-between mb-1">
      <h2 class="text-sm font-semibold">{{ i18n.t('admin_ai_title') }}</h2>
      <span
        class="text-[11px] px-2 py-0.5 rounded"
        :class="aiStore.aiConfig?.configured
          ? 'bg-green-500/10 text-green-600 dark:text-green-400'
          : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'"
      >{{ aiStore.aiConfig?.configured ? i18n.t('admin_ai_ready') : i18n.t('admin_ai_not_ready') }}</span>
    </div>
    <p class="text-xs text-muted dark:text-gray-400 mb-4">{{ i18n.t('admin_ai_hint') }}</p>

    <p v-if="aiStore.aiLoading" class="text-xs text-muted">{{ i18n.t('admin_loading') }}</p>
    <p v-if="aiStore.aiError" class="text-xs text-red-500 mb-3 break-words">{{ aiStore.aiError }}</p>

    <template v-if="!aiStore.aiLoading && aiStore.aiConfig">
      <div class="flex items-center justify-between mb-4">
        <span class="text-sm">{{ i18n.t('admin_ai_enable') }}</span>
        <button @click="aiStore.aiForm.enabled = !aiStore.aiForm.enabled" :class="aiStore.aiForm.enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'" class="relative w-11 h-6 rounded-full transition-colors duration-200">
          <span :class="aiStore.aiForm.enabled ? 'translate-x-5' : 'translate-x-0.5'" class="absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200"></span>
        </button>
      </div>

      <AiProviderList
        :title="i18n.t('admin_ai_profiles_title')"
        :hint="i18n.t(aiStore.aiProfileSource === 'profiles' ? 'admin_ai_profiles_hint' : 'admin_ai_profiles_single_hint')"
        :profiles="aiStore.aiProfiles"
        :selected-id="aiStore.aiActiveProfileId"
        :active-id="aiStore.aiConfig.active_profile_id || aiStore.aiProfiles[0]?.id || ''"
        @add="aiStore.addAIProfile()"
        @select="aiStore.selectAIProfile"
        @duplicate="aiStore.duplicateAIProfile"
        @remove="aiStore.removeAIProfile"
      />

      <template v-if="aiStore.aiActiveProfile">
        <div class="mb-3">
          <label class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_profile_name') }}</label>
          <input
            v-model="aiStore.aiActiveProfile.name"
            :placeholder="i18n.t('admin_ai_profile_placeholder')"
            class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
          />
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
          <label class="block">
            <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_provider') }}</span>
            <select
              v-model="aiStore.aiActiveProfile.provider"
              @change="aiStore.applyProviderPreset(aiStore.aiActiveProfile.provider)"
              class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
            >
              <option v-for="p in aiStore.aiConfig.providers" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
          </label>
          <label class="block">
            <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_model') }}</span>
            <div class="flex gap-2">
              <input v-model="aiStore.aiActiveProfile.model" class="flex-1 min-w-0 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
              <button
                type="button"
                @click="aiStore.loadAIModels"
                :disabled="aiStore.aiModelsLoading"
                class="px-2 py-2 rounded border border-border dark:border-gray-700 text-xs hover:bg-accent/10 disabled:opacity-50 shrink-0"
              >{{ aiStore.aiModelsLoading ? i18n.t('admin_ai_models_fetching') : i18n.t('admin_ai_models_fetch') }}</button>
            </div>
          </label>
        </div>

        <div v-if="aiStore.chatModelOptions.length" class="mb-3">
          <span class="block text-[11px] text-muted dark:text-gray-400 mb-1">
            {{ i18n.t('admin_ai_models_available', { n: aiStore.chatModelOptions.length }) }}
          </span>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="model in aiStore.chatModelOptions"
              :key="model"
              type="button"
              @click="aiStore.applyModel('model', model)"
              class="text-[11px] px-2 py-0.5 rounded border transition-colors"
              :class="aiStore.aiActiveProfile.model === model
                ? 'border-accent text-accent bg-accent/10'
                : 'border-border dark:border-gray-700 text-muted dark:text-gray-400 hover:text-accent'"
            >{{ model }}</button>
          </div>
        </div>

        <div class="mb-3">
          <label class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_base_url') }}</label>
          <input v-model="aiStore.aiActiveProfile.base_url" :placeholder="aiStore.aiActiveProfile.effective_base_url" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          <span class="block text-[11px] text-muted mt-1">{{ i18n.t('admin_ai_base_url_hint') }}</span>
        </div>

        <div class="mb-3">
          <label class="block text-xs text-muted dark:text-gray-400 mb-1">
            {{ i18n.t('admin_ai_api_key') }}
            <span v-if="aiStore.aiActiveProfile.api_key_set" class="text-green-600 dark:text-green-400">
              {{ i18n.t('admin_ai_key_saved', { hint: aiStore.aiActiveProfile.api_key_hint }) }}
            </span>
          </label>
          <div class="flex gap-2">
            <input
              v-model="aiStore.aiActiveProfile.api_key_input"
              type="password"
              autocomplete="new-password"
              :placeholder="aiStore.aiActiveProfile.api_key_set ? i18n.t('admin_ai_key_keep') : 'sk-...'"
              class="flex-1 min-w-0 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
            />
            <button
              v-if="aiStore.aiActiveProfile.api_key_set"
              type="button"
              @click="aiStore.clearAIProfileKey(aiStore.aiActiveProfile.id)"
              class="px-2 py-2 rounded border border-border dark:border-gray-700 text-xs hover:bg-accent/10 shrink-0"
            >{{ i18n.t('admin_ai_profile_clear_key') }}</button>
          </div>
          <span class="block text-[11px] text-muted mt-1">{{ i18n.t('admin_ai_key_hint') }}</span>
        </div>
      </template>

      <p v-if="aiStore.aiModelsError" class="text-xs text-amber-600 dark:text-amber-400 mb-2 break-words">{{ aiStore.aiModelsError }}</p>

      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
        <label class="block">
          <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_temperature') }}</span>
          <input v-model.number="aiStore.aiForm.temperature" type="number" step="0.1" min="0" max="2" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
        </label>
        <label class="block">
          <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_max_tokens') }}</span>
          <input v-model.number="aiStore.aiForm.max_tokens" type="number" min="1" max="100000" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
        </label>
        <label class="block">
          <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_timeout') }}</span>
          <input v-model.number="aiStore.aiForm.timeout" type="number" min="5" max="900" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
        </label>
        <label class="block">
          <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_context_chars') }}</span>
          <input v-model.number="aiStore.aiForm.context_chars" type="number" min="2000" max="200000" step="1000" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
        </label>
      </div>

      <div class="flex items-center justify-between mb-3 p-3 rounded border border-border dark:border-gray-700">
        <div>
          <span class="text-sm block">{{ i18n.t('admin_ai_use_proxy') }}</span>
          <span class="text-[11px] text-muted dark:text-gray-400">
            {{ i18n.t('admin_ai_use_proxy_hint') }}
            <template v-if="aiStore.aiConfig.crawler_proxy?.url">{{ i18n.t('admin_ai_proxy_available', { url: aiStore.aiConfig.crawler_proxy.url }) }}</template>
          </span>
        </div>
        <button @click="aiStore.aiForm.use_proxy = !aiStore.aiForm.use_proxy" :class="aiStore.aiForm.use_proxy ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'" class="relative w-11 h-6 rounded-full transition-colors duration-200 shrink-0">
          <span :class="aiStore.aiForm.use_proxy ? 'translate-x-5' : 'translate-x-0.5'" class="absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200"></span>
        </button>
      </div>
      <div v-if="aiStore.aiForm.use_proxy" class="mb-3">
        <label class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_proxy_url') }}</label>
        <input v-model="aiStore.aiForm.proxy_url" :placeholder="aiStore.aiConfig.crawler_proxy?.url || 'http://127.0.0.1:7890'" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      </div>

      <div class="flex flex-wrap gap-2 items-center">
        <button @click="aiStore.saveAIConfig" :disabled="aiStore.aiSaving" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
          {{ aiStore.aiSaving ? i18n.t('admin_saving') : i18n.t('admin_save') }}
        </button>
        <button @click="aiStore.testAIConfig" :disabled="aiStore.aiTesting" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-accent/10 disabled:opacity-50">
          {{ aiStore.aiTesting ? i18n.t('admin_ai_testing') : i18n.t('admin_ai_test') }}
        </button>
        <span v-if="aiStore.aiSaved" class="text-xs text-green-600 dark:text-green-400">{{ i18n.t('admin_ai_saved') }}</span>
      </div>

      <div v-if="aiStore.aiTestResult" class="mt-4 space-y-2 text-xs">
        <div class="p-3 rounded border" :class="aiStore.aiTestResult.chat?.ok ? 'border-green-500/40 bg-green-500/5' : 'border-red-500/40 bg-red-500/5'">
          <p class="font-medium mb-1">{{ i18n.t('admin_ai_test_chat') }}</p>
          <p v-if="aiStore.aiTestResult.chat?.ok" class="text-muted dark:text-gray-400 break-words">
            {{ i18n.t('admin_ai_test_ok', {
              model: aiStore.aiTestResult.chat.model,
              ms: aiStore.aiTestResult.chat.latency_ms,
            }) }}
            <br />{{ i18n.t('admin_ai_test_endpoint', { url: aiStore.aiTestResult.chat.endpoint }) }}
            <span v-if="aiStore.aiTestResult.chat.proxy"><br />{{ i18n.t('admin_ai_test_proxy', { url: aiStore.aiTestResult.chat.proxy }) }}</span>
            <span v-else><br />{{ i18n.t('admin_ai_test_direct') }}</span>
          </p>
          <p v-else class="text-red-500 break-words">{{ aiStore.aiTestResult.chat?.error }}</p>
          <div v-if="!aiStore.aiTestResult.chat?.ok && aiStore.aiTestResult.available_models?.length" class="mt-2">
            <span class="block text-[11px] text-muted dark:text-gray-400 mb-1">
              {{ i18n.t('admin_ai_models_apply') }}
            </span>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="model in aiStore.aiTestResult.available_models"
                :key="model"
                type="button"
                @click="aiStore.applyModel('model', model)"
                class="text-[11px] px-2 py-0.5 rounded border transition-colors"
                :class="aiStore.aiForm.model === model
                  ? 'border-accent text-accent bg-accent/10'
                  : 'border-border dark:border-gray-700 text-muted dark:text-gray-400 hover:text-accent'"
              >{{ model }}</button>
            </div>
            <p class="text-[11px] text-muted dark:text-gray-400 mt-1">{{ i18n.t('admin_ai_models_apply_hint') }}</p>
          </div>
        </div>
        <div v-if="aiStore.aiTestResult.embeddings" class="p-3 rounded border" :class="aiStore.aiTestResult.embeddings.ok ? 'border-green-500/40 bg-green-500/5' : 'border-amber-500/40 bg-amber-500/5'">
          <p class="font-medium mb-1">{{ i18n.t('admin_ai_test_embeddings') }}</p>
          <p v-if="aiStore.aiTestResult.embeddings.ok" class="text-muted dark:text-gray-400 break-words">
            {{ i18n.t('admin_ai_test_embedding_ok', {
              model: aiStore.aiTestResult.embeddings.model,
              dim: aiStore.aiTestResult.embeddings.dimension,
            }) }}
          </p>
          <p v-else class="text-amber-600 dark:text-amber-400 break-words">{{ aiStore.aiTestResult.embeddings.error }}</p>
        </div>
      </div>
    </template>
  </div>

  <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
    <h2 class="text-sm font-semibold mb-1">{{ i18n.t('admin_ai_embedding_title') }}</h2>
    <p class="text-xs text-muted dark:text-gray-400 mb-4">{{ i18n.t('admin_ai_embedding_hint') }}</p>

    <AiProviderList
      :title="i18n.t('admin_ai_embedding_profiles_title')"
      :hint="i18n.t('admin_ai_embedding_profiles_hint')"
      :profiles="aiStore.embeddingProfiles"
      :selected-id="aiStore.selectedEmbeddingId"
      :active-id="aiStore.aiConfig?.active_embedding_profile_id || ''"
      :inherited-label="i18n.t('admin_ai_embedding_same')"
      @add="aiStore.addEmbeddingProfile()"
      @select="aiStore.selectedEmbeddingId = $event"
      @duplicate="aiStore.addEmbeddingProfile"
      @remove="aiStore.removeEmbeddingProfile"
    />
    <template v-if="aiStore.selectedEmbeddingProfile">
    <label class="mb-3 block">
      <span class="mb-1 block text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_ai_profile_name') }}</span>
      <input v-model="aiStore.selectedEmbeddingProfile.name" class="w-full rounded border border-border bg-paper px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800" />
    </label>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
      <label class="block">
        <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_embedding_provider') }}</span>
        <select :value="aiStore.selectedEmbeddingProfile.provider" @change="aiStore.applyEmbeddingPreset(($event.target as HTMLSelectElement).value)" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
          <option value="">{{ i18n.t('admin_ai_embedding_same') }}</option>
          <option v-for="p in (aiStore.aiConfig?.providers || []).filter((p: any) => p.embeddings)" :key="p.value" :value="p.value">{{ p.label }}</option>
        </select>
      </label>
      <label class="block">
        <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_embedding_model') }}</span>
        <input v-model="aiStore.selectedEmbeddingProfile.model" :placeholder="aiStore.selectedEmbeddingProfile.effective_model" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      </label>
    </div>
    <div v-if="!aiStore.selectedEmbeddingProfile.provider && !aiStore.selectedEmbeddingProfile.base_url && aiStore.aiModels.length" class="mb-3">
      <span class="block text-[11px] text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_models_embedding') }}</span>
      <div class="flex flex-wrap gap-1.5">
        <button
          v-for="model in aiStore.embeddingModelOptions"
          :key="model"
          type="button"
          @click="aiStore.applyModel('embedding_model', model)"
          class="text-[11px] px-2 py-0.5 rounded border transition-colors"
          :class="aiStore.selectedEmbeddingProfile.model === model
            ? 'border-accent text-accent bg-accent/10'
            : 'border-border dark:border-gray-700 text-muted dark:text-gray-400 hover:text-accent'"
        >{{ model }}</button>
      </div>
    </div>
    <label class="mb-3 block">
      <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_embedding_base_url') }}</span>
      <input v-model="aiStore.selectedEmbeddingProfile.base_url" :placeholder="aiStore.selectedEmbeddingProfile.effective_base_url" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
    </label>
    <div class="mb-3">
      <label class="block">
        <span class="mb-1 block text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_ai_embedding_key') }}</span>
        <input v-model="aiStore.selectedEmbeddingProfile.api_key_input" type="password" autocomplete="new-password" :placeholder="aiStore.selectedEmbeddingProfile.api_key_set ? i18n.t('admin_ai_key_keep') : !aiStore.selectedEmbeddingProfile.provider && !aiStore.selectedEmbeddingProfile.base_url ? i18n.t('admin_ai_embedding_key_same') : ''" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      </label>
      <button v-if="aiStore.selectedEmbeddingProfile.api_key_set" type="button" @click="aiStore.clearEmbeddingKey" class="mt-1 py-1 text-[11px] text-red-500 hover:text-red-700">{{ i18n.t('admin_ai_profile_clear_key') }}</button>
    </div>
    </template>

    <div class="flex items-center justify-between mb-3">
      <div>
        <span class="text-sm block">{{ i18n.t('admin_ai_rag_enable') }}</span>
        <span class="text-[11px] text-muted dark:text-gray-400">{{ i18n.t('admin_ai_rag_hint') }}</span>
      </div>
      <button @click="aiStore.aiForm.rag_enabled = !aiStore.aiForm.rag_enabled" :class="aiStore.aiForm.rag_enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'" class="relative w-11 h-6 rounded-full transition-colors duration-200 shrink-0">
        <span :class="aiStore.aiForm.rag_enabled ? 'translate-x-5' : 'translate-x-0.5'" class="absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200"></span>
      </button>
    </div>
    <div class="mb-3 max-w-[10rem]">
      <label class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_rag_top_k') }}</label>
      <input v-model.number="aiStore.aiForm.rag_top_k" type="number" min="1" max="20" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
    </div>

    <div class="flex items-center justify-between mb-3 p-3 rounded border border-border dark:border-gray-700">
      <div class="pr-3">
        <span class="text-sm block">{{ i18n.t('admin_ai_rag_auto_index') }}</span>
        <span class="text-[11px] text-muted dark:text-gray-400">{{ i18n.t('admin_ai_rag_auto_hint') }}</span>
      </div>
      <button @click="aiStore.aiForm.rag_auto_index = !aiStore.aiForm.rag_auto_index" :class="aiStore.aiForm.rag_auto_index ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'" class="relative w-11 h-6 rounded-full transition-colors duration-200 shrink-0">
        <span :class="aiStore.aiForm.rag_auto_index ? 'translate-x-5' : 'translate-x-0.5'" class="absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200"></span>
      </button>
    </div>

    <div class="flex items-center justify-between mb-3 p-3 rounded border border-border dark:border-gray-700">
      <div>
        <span class="text-sm block">{{ i18n.t('admin_ai_auto_diagnose') }}</span>
        <span class="text-[11px] text-muted dark:text-gray-400">{{ i18n.t('admin_ai_auto_diagnose_hint') }}</span>
      </div>
      <button @click="aiStore.aiForm.auto_diagnose = !aiStore.aiForm.auto_diagnose" :class="aiStore.aiForm.auto_diagnose ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'" class="relative w-11 h-6 rounded-full transition-colors duration-200 shrink-0">
        <span :class="aiStore.aiForm.auto_diagnose ? 'translate-x-5' : 'translate-x-0.5'" class="absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200"></span>
      </button>
    </div>

    <button @click="aiStore.saveAIConfig" :disabled="aiStore.aiSaving" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
      {{ aiStore.aiSaving ? i18n.t('admin_saving') : i18n.t('admin_save') }}
    </button>
  </div>

  <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
    <h2 class="text-sm font-semibold mb-1">{{ i18n.t('admin_ai_index_title') }}</h2>
    <p class="text-xs text-muted dark:text-gray-400 mb-4">{{ i18n.t('admin_ai_index_hint') }}</p>

    <div class="flex gap-2 mb-3">
      <input v-model="aiStore.aiIndexBookId" :placeholder="i18n.t('admin_ai_index_placeholder')" class="min-w-0 flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      <button @click="aiStore.indexBookForRAG(forceChapterIndex)" :disabled="aiStore.aiIndexBusy || !aiStore.aiIndexBookId.trim()" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 shrink-0">
        {{ aiStore.aiIndexBusy ? i18n.t('admin_ai_index_running') : i18n.t('admin_ai_index_run') }}
      </button>
    </div>
    <label class="flex items-center gap-2 mb-3 text-xs text-muted dark:text-gray-400">
      <input v-model="forceChapterIndex" type="checkbox" :disabled="aiStore.aiIndexBusy" />
      {{ i18n.t('admin_ai_index_force') }}
    </label>
    <p v-if="aiStore.aiIndexError" class="text-xs text-red-500 mb-3 break-words">{{ aiStore.aiIndexError }}</p>
    <p v-if="aiStore.aiIndexResult" class="text-xs text-muted dark:text-gray-400 mb-3">
      <template v-if="aiStore.aiIndexResult.skipped">
        {{ aiStore.aiIndexResult.reason || i18n.t('admin_ai_index_skipped') }}
      </template>
      <template v-else>{{ i18n.t('admin_ai_index_done', {
        chapters: aiStore.aiIndexResult.indexed_chapters ?? aiStore.aiIndexResult.chapters ?? 0,
        chunks: aiStore.aiIndexResult.chunks ?? 0,
        model: aiStore.aiIndexResult.model || '',
      }) }}</template>
    </p>

    <div class="mt-4 pt-4 border-t border-border dark:border-gray-700">
      <h3 class="text-xs font-medium mb-1">{{ i18n.t('admin_ai_rag_batch_title') }}</h3>
      <p class="text-[11px] text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_ai_rag_batch_hint') }}</p>

      <div class="grid grid-cols-2 gap-2 max-w-sm mb-3">
        <label class="text-[11px] text-muted">
          {{ i18n.t('admin_ai_rag_batch_limit') }}
          <input v-model.number="aiStore.ragBatchLimit" type="number" min="0" :disabled="jobRunning" class="mt-1 w-full px-2 py-1 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 disabled:opacity-60" />
        </label>
        <label class="text-[11px] text-muted">
          {{ i18n.t('admin_ai_rag_batch_chunks') }}
          <input v-model.number="aiStore.ragBatchChunks" type="number" min="0" :disabled="jobRunning" class="mt-1 w-full px-2 py-1 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 disabled:opacity-60" />
        </label>
      </div>

      <p class="text-[11px] text-amber-600 dark:text-amber-400 mb-3 break-words">
        {{ batchEstimate
          ? i18n.t('admin_ai_rag_batch_warn', batchEstimate)
          : i18n.t('admin_ai_rag_batch_warn_all') }}
      </p>

      <div class="flex flex-wrap gap-2 mb-3">
        <button @click="aiStore.startRagBatch" :disabled="aiStore.ragBatchBusy || jobRunning" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
          {{ aiStore.ragBatchBusy ? i18n.t('admin_ai_rag_batch_starting') : i18n.t('admin_ai_rag_batch_start') }}
        </button>
        <button v-if="jobRunning" @click="aiStore.cancelRagBatch" :disabled="jobCancelling" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm text-ink hover:bg-accent/5 disabled:opacity-50">
          {{ jobCancelling ? i18n.t('admin_ai_rag_batch_cancelling') : i18n.t('admin_ai_rag_batch_cancel') }}
        </button>
      </div>

      <p v-if="aiStore.ragBatchError" class="text-xs text-red-500 mb-2 break-words">{{ aiStore.ragBatchError }}</p>
      <p v-if="aiStore.ragBatchResult && (!aiStore.ragBatchResult.queued || jobRunning)" class="text-xs text-muted dark:text-gray-400 mb-2 break-words">
        <template v-if="aiStore.ragBatchResult.queued">
          {{ i18n.t('admin_ai_rag_batch_queued', {
            queued: aiStore.ragBatchResult.queued,
            candidates: aiStore.ragBatchResult.candidates ?? aiStore.ragBatchResult.queued,
          }) }}
        </template>
        <template v-else>{{ aiStore.ragBatchResult.message || i18n.t('admin_ai_rag_batch_none') }}</template>
      </p>
      <p v-if="aiStore.ragJobError" class="text-xs text-red-500 mb-2 break-words">{{ aiStore.ragJobError }}</p>

      <div v-if="job" class="mb-3 p-2 rounded border border-border dark:border-gray-700">
        <div class="flex items-center justify-between text-[11px] text-muted dark:text-gray-400 mb-1">
          <span>{{ jobPhaseLabel }}</span>
          <span v-if="jobShowsProgress">{{ jobProcessed }} / {{ jobTotal }}</span>
        </div>
        <div v-if="jobShowsProgress" class="h-1.5 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden mb-1">
          <div class="h-full bg-accent transition-all" :style="{ width: jobPercent + '%' }"></div>
        </div>
        <p v-if="jobCurrent" class="text-[11px] text-muted truncate">{{ i18n.t('admin_ai_rag_job_current', { title: jobCurrent }) }}</p>
        <p class="text-[11px] text-muted dark:text-gray-400">
          {{ i18n.t('admin_ai_rag_job_stats', {
            done: job.done || 0, skipped: job.skipped || 0,
            failed: job.failed || 0, chunks: job.chunks || 0,
          }) }}
        </p>
        <p v-if="job.discarded" class="text-[11px] text-muted dark:text-gray-400">
          {{ i18n.t('admin_ai_rag_job_discarded', { count: job.discarded }) }}
        </p>
        <details v-if="jobLastError" :key="job.started_at" class="text-[11px] text-red-500">
          <summary class="cursor-pointer">{{ i18n.t('admin_ai_rag_job_error_details') }}</summary>
          <p class="break-words">{{ jobLastError }}</p>
        </details>
        <p class="text-[11px] text-muted dark:text-gray-400 mt-1">
          {{ i18n.t('admin_ai_rag_indexed_stats', {
            books: indexedStats.books, chunks: indexedStats.chunks, size: indexedStats.mb,
          }) }}
        </p>
      </div>
    </div>

    <div class="mt-4 pt-4 border-t border-border dark:border-gray-700">
      <h3 class="text-xs font-medium mb-1">{{ i18n.t('admin_ai_library_title') }}</h3>
      <p class="text-[11px] text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_ai_library_hint') }}</p>

      <div class="max-w-[12rem] mb-3">
        <label class="text-[11px] text-muted">
          {{ i18n.t('admin_ai_library_limit') }}
          <input v-model.number="aiStore.libraryLimit" type="number" min="0" :disabled="libraryRunning" class="mt-1 w-full px-2 py-1 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 disabled:opacity-60" />
        </label>
      </div>

      <p class="text-[11px] text-amber-600 dark:text-amber-400 mb-3 break-words">
        {{ libraryEstimate
          ? i18n.t('admin_ai_library_warn', libraryEstimate)
          : i18n.t('admin_ai_library_warn_all') }}
      </p>

      <div class="flex flex-wrap gap-2 mb-3">
        <button @click="aiStore.startLibraryBuild" :disabled="aiStore.libraryBusy || libraryRunning" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
          {{ aiStore.libraryBusy ? i18n.t('admin_ai_library_starting') : i18n.t('admin_ai_library_start') }}
        </button>
        <button v-if="libraryRunning" @click="aiStore.cancelLibraryBuild" :disabled="libraryCancelling" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm text-ink hover:bg-accent/5 disabled:opacity-50">
          {{ libraryCancelling ? i18n.t('admin_ai_library_cancelling') : i18n.t('admin_ai_library_cancel') }}
        </button>
      </div>

      <p v-if="aiStore.libraryError" class="text-xs text-red-500 mb-2 break-words">{{ aiStore.libraryError }}</p>

      <div v-if="library" class="mb-1 p-2 rounded border border-border dark:border-gray-700">
        <div class="flex items-center justify-between text-[11px] text-muted dark:text-gray-400 mb-1">
          <span>{{ libraryPhaseLabel }}</span>
          <span>{{ libraryDone }} / {{ libraryTotal }}</span>
        </div>
        <div class="h-1.5 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden mb-1">
          <div class="h-full bg-accent transition-all" :style="{ width: libraryPercent + '%' }"></div>
        </div>
        <p v-if="libraryCurrent" class="text-[11px] text-muted truncate">{{ i18n.t('admin_ai_library_current', { title: libraryCurrent }) }}</p>
        <p class="text-[11px] text-muted dark:text-gray-400">
          {{ i18n.t('admin_ai_library_stats', {
            books: libraryStats.books, vectors: libraryStats.vectors, size: libraryStats.mb,
          }) }}
        </p>
        <p v-if="library.error" class="text-[11px] text-red-500 break-words">{{ library.error }}</p>
      </div>
    </div>

    <div class="flex items-center justify-between mb-2">
      <span class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_ai_index_list') }}</span>
      <button @click="aiStore.loadIndexedBooks" class="text-[11px] text-accent hover:opacity-70">{{ i18n.t('admin_ai_index_refresh') }}</button>
    </div>
    <p v-if="aiStore.aiIndexedLoading" class="text-xs text-muted">{{ i18n.t('admin_loading') }}</p>
    <p v-else-if="!aiStore.aiIndexedBooks.length" class="text-xs text-muted">{{ i18n.t('admin_ai_index_empty') }}</p>
    <div v-else class="space-y-1.5 max-h-64 overflow-y-auto">
      <div v-for="entry in aiStore.aiIndexedBooks" :key="entry.book_id" class="flex items-center gap-3 text-xs p-2 rounded border border-border dark:border-gray-700">
        <span class="flex-1 truncate">{{ entry.title || entry.book_id }}</span>
        <span class="text-muted shrink-0">{{ i18n.t('admin_ai_index_entry', { chunks: entry.chunks, chapters: entry.chapters }) }}</span>
        <button @click="aiStore.deleteRAGIndex(entry.book_id)" class="text-red-400 hover:text-red-600 shrink-0">{{ i18n.t('admin_ai_index_delete') }}</button>
      </div>
    </div>
  </div>
</section>
</template>
