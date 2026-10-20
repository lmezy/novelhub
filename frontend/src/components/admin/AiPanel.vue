<script setup lang="ts">
import { useI18nStore } from "../../stores/i18n"
import { useAdminAIStore } from "../../stores/adminAI"

const i18n = useI18nStore()
const aiStore = useAdminAIStore()
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

      <!-- saved model providers -->
      <div class="mb-4 p-3 rounded border border-border dark:border-gray-700">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium">{{ i18n.t('admin_ai_profiles_title') }}</span>
          <button
            type="button"
            @click="aiStore.addAIProfile"
            class="text-[11px] px-2 py-0.5 rounded border border-border dark:border-gray-700 hover:bg-accent/10"
          >{{ i18n.t('admin_ai_profile_add') }}</button>
        </div>
        <p class="text-[11px] text-muted dark:text-gray-400 mb-2">
          {{ aiStore.aiProfileSource === 'profiles'
            ? i18n.t('admin_ai_profiles_hint')
            : i18n.t('admin_ai_profiles_single_hint') }}
        </p>

        <div v-if="!aiStore.aiProfiles.length" class="text-[11px] text-muted dark:text-gray-400">
          {{ i18n.t('admin_ai_profile_empty') }}
        </div>
        <div v-else class="space-y-1.5">
          <div
            v-for="profile in aiStore.aiProfiles"
            :key="profile.id"
            class="flex items-center gap-2 text-xs p-2 rounded border transition-colors cursor-pointer"
            :class="profile.id === aiStore.aiActiveProfileId
              ? 'border-accent bg-accent/5'
              : 'border-border dark:border-gray-700 hover:border-accent/60'"
            @click="aiStore.selectAIProfile(profile.id)"
          >
            <span class="flex-1 min-w-0 truncate">
              {{ profile.name || profile.provider_label || profile.provider }}
              <span class="text-muted dark:text-gray-400">
                · {{ profile.provider_label || profile.provider }}
                <template v-if="profile.model">· {{ profile.model }}</template>
              </span>
            </span>
            <span
              v-if="profile.id === aiStore.aiActiveProfileId"
              class="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent shrink-0"
            >{{ i18n.t('admin_ai_profile_in_use') }}</span>
            <span v-if="profile.api_key_set" class="text-[10px] text-green-600 dark:text-green-400 shrink-0">
              {{ i18n.t('admin_ai_profile_key_saved') }}
            </span>
            <button
              type="button"
              class="text-[11px] text-accent hover:opacity-70 shrink-0"
              @click.stop="aiStore.duplicateAIProfile(profile.id)"
            >{{ i18n.t('admin_ai_profile_duplicate') }}</button>
            <button
              type="button"
              class="text-[11px] text-red-400 hover:text-red-600 shrink-0"
              @click.stop="aiStore.removeAIProfile(profile.id)"
            >{{ i18n.t('admin_ai_profile_delete') }}</button>
          </div>
        </div>
      </div>

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

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
      <label class="block">
        <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_embedding_provider') }}</span>
        <select v-model="aiStore.aiForm.embedding_provider" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
          <option value="">{{ i18n.t('admin_ai_embedding_same') }}</option>
          <option v-for="p in (aiStore.aiConfig?.providers || []).filter((p: any) => p.embeddings)" :key="p.value" :value="p.value">{{ p.label }}</option>
        </select>
      </label>
      <label class="block">
        <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_embedding_model') }}</span>
        <input v-model="aiStore.aiForm.embedding_model" :placeholder="aiStore.aiConfig?.effective_embedding_model" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      </label>
    </div>
    <div v-if="aiStore.aiModels.length" class="mb-3">
      <span class="block text-[11px] text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_models_embedding') }}</span>
      <div class="flex flex-wrap gap-1.5">
        <button
          v-for="model in aiStore.embeddingModelOptions"
          :key="model"
          type="button"
          @click="aiStore.applyModel('embedding_model', model)"
          class="text-[11px] px-2 py-0.5 rounded border transition-colors"
          :class="aiStore.aiForm.embedding_model === model
            ? 'border-accent text-accent bg-accent/10'
            : 'border-border dark:border-gray-700 text-muted dark:text-gray-400 hover:text-accent'"
        >{{ model }}</button>
      </div>
    </div>
    <div class="mb-3">
      <label class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_embedding_base_url') }}</label>
      <input v-model="aiStore.aiForm.embedding_base_url" :placeholder="aiStore.aiConfig?.effective_embedding_base_url" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
    </div>
    <div class="mb-3">
      <label class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_ai_embedding_key') }}</label>
      <input v-model="aiStore.aiEmbeddingKeyInput" type="password" autocomplete="new-password" :placeholder="aiStore.aiConfig?.embedding_api_key_set ? i18n.t('admin_ai_key_keep') : i18n.t('admin_ai_embedding_key_same')" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
    </div>

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
      <input v-model="aiStore.aiIndexBookId" :placeholder="i18n.t('admin_ai_index_placeholder')" class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      <button @click="aiStore.indexBookForRAG" :disabled="aiStore.aiIndexBusy || !aiStore.aiIndexBookId.trim()" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 shrink-0">
        {{ aiStore.aiIndexBusy ? i18n.t('admin_ai_index_running') : i18n.t('admin_ai_index_run') }}
      </button>
    </div>
    <p v-if="aiStore.aiIndexError" class="text-xs text-red-500 mb-3 break-words">{{ aiStore.aiIndexError }}</p>
    <p v-if="aiStore.aiIndexResult" class="text-xs text-muted dark:text-gray-400 mb-3">
      {{ i18n.t('admin_ai_index_done', {
        chapters: aiStore.aiIndexResult.indexed_chapters ?? aiStore.aiIndexResult.chapters ?? 0,
        chunks: aiStore.aiIndexResult.chunks ?? 0,
        model: aiStore.aiIndexResult.model || '',
      }) }}
      <span v-if="aiStore.aiIndexResult.skipped">{{ i18n.t('admin_ai_index_skipped') }}</span>
    </p>

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
