<script setup lang="ts">
import { useI18nStore } from "../../stores/i18n"
import { useAdminApprovalsStore } from "../../stores/adminApprovals"

const i18n = useI18nStore()
const approvalsStore = useAdminApprovalsStore()
</script>

<template>
<section class="space-y-6">
  <div class="flex items-center justify-between mb-4">
    <h2 class="text-lg font-semibold">{{ i18n.t('admin_pending_approvals') }}</h2>
    <button @click="approvalsStore.loadApprovals" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-surface transition-colors">{{ i18n.t('admin_refresh') }}</button>
  </div>
  <p class="text-xs text-muted dark:text-gray-400 -mt-3 mb-3">{{ i18n.t('admin_approval_sync_hint') }}</p>
  <p v-if="approvalsStore.approvalError" class="text-sm text-red-600 mb-3">{{ approvalsStore.approvalError }}</p>
  <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
    <div v-for="a in approvalsStore.approvals" :key="a.id" class="px-4 py-3 flex items-start justify-between flex-wrap gap-2">
      <div class="min-w-0 flex-1">
        <div class="flex items-center flex-wrap gap-2">
          <span class="text-xs px-1.5 py-0.5 rounded-full" :class="a.action === 'create' ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' : a.action === 'confirm_r18' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300' : a.action === 'update' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' : 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300'">{{ a.action === 'confirm_r18' ? i18n.t('admin_approval_r18_conflict') : a.action === 'update' ? i18n.t('admin_approval_update') : a.action }}</span>
          <span v-if="a.source_data?.origin === 'ai'" class="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent">{{ i18n.t('admin_approval_from_ai') }}</span>
          <template v-if="a.action === 'create' && a.source_data">
            <span class="text-sm font-medium">{{ a.source_data.name }}</span>
            <span class="text-xs text-muted dark:text-gray-400">{{ a.source_data.id }} ({{ a.source_data.plugin_name }})</span>
          </template>
          <template v-else-if="a.action === 'update'">
            <span class="text-sm font-medium">{{ i18n.t('admin_approval_update_source', { id: a.source_id }) }}</span>
          </template>
          <template v-else-if="a.source_id">
            <span class="text-sm font-medium">{{ i18n.t('admin_delete_source', { id: a.source_id }) }}</span>
          </template>
          <template v-else-if="a.action === 'confirm_r18' && a.source_data">
            <span class="text-sm font-medium">{{ i18n.t('admin_approval_r18_conflict') }}</span>
            <span class="text-xs text-muted dark:text-gray-400">{{ a.source_data.title }}</span>
          </template>
          <span class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_by_user', { id: a.submitter_username || a.user_id?.slice(0, 8) }) }}</span>
        </div>

        <div v-if="a.action === 'update' && a.source_data?.diff?.length" class="mt-2 space-y-1.5">
          <p v-if="a.source_data.summary" class="text-xs text-muted dark:text-gray-400">{{ a.source_data.summary }}</p>
          <div v-for="(d, i) in a.source_data.diff" :key="i" class="p-2 rounded border border-border dark:border-gray-700 text-xs">
            <div class="flex items-center gap-2 mb-1">
              <code class="text-accent break-all">{{ d.path }}</code>
              <span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800">{{ d.risk }}</span>
              <span v-if="d.mismatch" class="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300">{{ i18n.t('sync_ai_stale') }}</span>
            </div>
            <p class="text-muted dark:text-gray-400 break-words">{{ i18n.t('sync_ai_current') }}<code class="break-all">{{ d.current || '—' }}</code></p>
            <p class="text-muted dark:text-gray-400 break-words">{{ i18n.t('sync_ai_suggest') }}<code class="break-all">{{ d.new_text ?? d.new }}</code></p>
            <p v-if="d.reason" class="mt-1 text-muted dark:text-gray-400 break-words">{{ d.reason }}</p>
          </div>
        </div>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <button @click="approvalsStore.reviewChange(a.id, 'approve')" :disabled="approvalsStore.approvalReviewing[a.id]" class="px-3 py-1 rounded bg-green-600 text-white text-xs font-medium hover:bg-green-700 disabled:opacity-50">{{ i18n.t('admin_approve') }}</button>
        <button @click="approvalsStore.reviewChange(a.id, 'reject')" :disabled="approvalsStore.approvalReviewing[a.id]" class="px-3 py-1 rounded bg-red-500 text-white text-xs font-medium hover:bg-red-600 disabled:opacity-50">{{ i18n.t('admin_reject') }}</button>
      </div>
    </div>
    <p v-if="approvalsStore.approvals.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_approvals') }}</p>
  </div>
</section>
</template>
