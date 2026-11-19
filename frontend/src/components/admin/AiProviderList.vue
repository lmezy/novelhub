<script setup lang="ts">
import type { AIProviderDraft } from "../../stores/adminAI"
import { useI18nStore } from "../../stores/i18n"

defineProps<{
  title: string
  hint: string
  profiles: AIProviderDraft[]
  selectedId: string
  activeId: string
  inheritedLabel?: string
}>()

const emit = defineEmits<{
  add: []
  select: [id: string]
  duplicate: [id: string]
  remove: [id: string]
}>()

const i18n = useI18nStore()
</script>

<template>
  <div class="mb-4 rounded border border-border p-3 dark:border-gray-700">
    <div class="mb-2 flex items-center justify-between gap-2">
      <span class="text-xs font-medium">{{ title }}</span>
      <button type="button" @click="emit('add')" class="shrink-0 rounded border border-border px-2 py-1 text-[11px] hover:bg-accent/10 dark:border-gray-700">
        {{ i18n.t('admin_ai_profile_add') }}
      </button>
    </div>
    <p class="mb-2 text-[11px] text-muted dark:text-gray-400">{{ hint }}</p>
    <p v-if="!profiles.length" class="text-[11px] text-muted dark:text-gray-400">{{ i18n.t('admin_ai_profile_empty') }}</p>
    <div v-else class="space-y-1.5">
      <div
        v-for="profile in profiles"
        :key="profile.id"
        class="flex items-center gap-2 rounded border px-2 text-xs"
        :class="profile.id === selectedId ? 'border-accent bg-accent/5' : 'border-border dark:border-gray-700'"
      >
        <button type="button" :aria-pressed="profile.id === selectedId" @click="emit('select', profile.id)" class="min-w-0 flex-1 truncate py-2.5 text-left">
          {{ profile.name || profile.provider_label || profile.provider || inheritedLabel }}
          <span v-if="profile.name && profile.provider_label" class="text-muted dark:text-gray-400"> · {{ profile.provider_label }}</span>
          <span v-if="profile.model || profile.effective_model" class="text-muted dark:text-gray-400"> · {{ profile.model || profile.effective_model }}</span>
        </button>
        <span v-if="profile.id === activeId" class="shrink-0 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] text-accent">{{ i18n.t('admin_ai_profile_in_use') }}</span>
        <span v-if="profile.api_key_set" class="hidden shrink-0 text-[10px] text-green-600 dark:text-green-400 sm:inline">{{ i18n.t('admin_ai_profile_key_saved') }}</span>
        <button type="button" @click="emit('duplicate', profile.id)" class="shrink-0 py-2 text-[11px] text-accent hover:opacity-70">{{ i18n.t('admin_ai_profile_duplicate') }}</button>
        <button type="button" @click="emit('remove', profile.id)" class="shrink-0 py-2 text-[11px] text-red-400 hover:text-red-600">{{ i18n.t('admin_ai_profile_delete') }}</button>
      </div>
    </div>
  </div>
</template>
