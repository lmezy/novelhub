<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { api } from "../api/client"
import { useAuthStore } from "../stores/auth"
import { useCrawlStore } from "../stores/crawl"
import { useI18nStore } from "../stores/i18n"
import NavBar from "../components/NavBar.vue"

const auth = useAuthStore()
const i18n = useI18nStore()
const crawlStore = useCrawlStore()
const sources = ref<any[]>([])
const tasks = ref<any[]>([])
const selectedSourceIds = ref<string[]>([])
const starting = ref(false)
const loadingTasks = ref(false)
const pageError = ref("")
const autoSyncEnabled = ref(false)
const autoSyncTime = ref("03:00")
const autoSyncIntervalHours = ref(0)
const autoSyncSaving = ref(false)
const autoSyncSaved = ref(false)
const autoSyncError = ref("")
const syncMaxPages = ref(20)
const bookshelfSyncing = ref(false)
const bookshelfResults = ref<any[]>([])
const historyNotice = ref("")

const activeTask = computed(() => crawlStore.activeTask)
const enabledSources = computed(() => sources.value.filter((s: any) => s.enabled))
const syncableSources = computed(() =>
  enabledSources.value.filter((s: any) => auth.isAdmin || s.owner_id === auth.user?.id)
)
// Sources whose 拉取间隔 is configured: the sync below will pace itself at that
// interval, which for a full-site task can mean hours, so say so up front.
const throttledSources = computed(() =>
  selectedSourceIds.value.filter((id) => {
    const source = sources.value.find((s: any) => s.id === id)
    return (source?.sync_interval_seconds || 0) > 0
  })
)

const activeProgress = computed(() => {
  const task = activeTask.value
  if (!task) return 0
  if (!task.max_pages) return 0
  const max = task.max_pages || 1
  const pages = task.progress?.pages_checked || 0
  return Math.min(100, Math.round((pages / max) * 100))
})

// Recent tasks: what is running now on top, then what is queued, then what the
// user paused -- the same ranking the API sorts by.  Sorting again on the client
// keeps a task that changes status in place, and because no finished task is
// ranked, an active one can never be pushed out of the list.
const taskStatusRank: Record<string, number> = { running: 0, pending: 1, paused: 2 }
const orderedTasks = computed(() =>
  [...tasks.value].sort((a: any, b: any) => {
    const rankDiff = (taskStatusRank[a.status] ?? 2) - (taskStatusRank[b.status] ?? 2)
    if (rankDiff !== 0) return rankDiff
    return new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()
  })
)

const terminal = ["completed", "failed", "cancelled", "completed_with_errors"]
// Polling only tells us something new while the worker can still move the task.
const pollable = ["pending", "running"]
// What the server lets us delete: anything that is not waiting for, or
// occupying, a place in the queue.
const deletable = ["paused", ...terminal]

function sourceName(task: any) {
  return sources.value.find((s) => s.id === task.source)?.name || task.source
}

function statusText(status: string) {
  const map: Record<string, string> = {
    pending: i18n.t('sync_queued'),
    running: i18n.t('sync_running'),
    paused: i18n.t('sync_paused'),
    completed: i18n.t('sync_completed'),
    completed_with_errors: i18n.t('sync_completed_with_errors'),
    failed: i18n.t('sync_failed_status'),
    cancelled: i18n.t('sync_cancelled'),
  }
  return map[status] || status
}

async function loadSources() {
  try {
    sources.value = await api.get<any[]>("/sources")
  } catch {
    sources.value = []
  }
}

async function loadTasks() {
  loadingTasks.value = true
  pageError.value = ""
  try {
    tasks.value = await api.get<any[]>("/crawl/tasks?limit=100")
    const active = tasks.value.find((t) => ["pending", "running", "paused"].includes(t.status))
    const selectedStillHere = crawlStore.activeTask?.id
      ? tasks.value.some((t) => t.id === crawlStore.activeTask.id)
      : false
    if (selectedStillHere) {
      const current = tasks.value.find((t) => t.id === crawlStore.activeTask?.id)
      if (current) crawlStore.activeTask = current
    } else if (active) {
      await crawlStore.setTask(active)
    } else {
      crawlStore.clear()
    }
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : i18n.t('sync_failed_load_tasks')
  } finally {
    loadingTasks.value = false
  }
}

async function selectTask(task: any) {
  if (!task) return
  await crawlStore.setTask(task)
  await loadDiagnosis(task.id)
}

// ---------------------------------------------------------------------------
// AI diagnosis of a failed task (admin only)
//
// The AI explains the failure and may propose book-source changes; nothing is
// written until the proposal is approved in 设置 → 审批.
// ---------------------------------------------------------------------------
const diagnosis = ref<any>(null)
const diagnosisLoading = ref(false)
const diagnosisError = ref("")
const diagnosisProposing = ref(false)
const diagnosisNotice = ref("")

const canDiagnose = computed(() => auth.isAdmin)
const diagnosisWorthwhile = computed(() => {
  const task = activeTask.value
  if (!task) return false
  if (task.status === "failed") return true
  if (task.status !== "completed_with_errors") return false
  return Number(task.result?.books_failed || 0) > 0
    || Number(task.result?.chapters_failed || 0) > 0
})

async function loadDiagnosis(taskId: string) {
  diagnosis.value = null
  diagnosisError.value = ""
  diagnosisNotice.value = ""
  if (!auth.isAdmin || !taskId) return
  try {
    diagnosis.value = await api.get<any>("/ai/diagnose/" + taskId)
  } catch {
    // 404 simply means "not analysed yet".
    diagnosis.value = null
  }
}

async function runDiagnosis() {
  const task = activeTask.value
  if (!task) return
  diagnosisLoading.value = true
  diagnosisError.value = ""
  diagnosisNotice.value = ""
  try {
    diagnosis.value = await api.post<any>(
      "/ai/diagnose/" + task.id, { force: Boolean(diagnosis.value) },
    )
  } catch (e) {
    diagnosisError.value = e instanceof Error ? e.message : i18n.t('sync_ai_failed')
  } finally {
    diagnosisLoading.value = false
  }
}

async function proposeSourceChange() {
  const task = activeTask.value
  if (!task) return
  diagnosisProposing.value = true
  diagnosisError.value = ""
  try {
    const res = await api.post<any>("/ai/diagnose/" + task.id + "/propose")
    diagnosisNotice.value = res.message || i18n.t('sync_ai_proposed')
    if (diagnosis.value) diagnosis.value.change_id = res.change_id
  } catch (e) {
    diagnosisError.value = e instanceof Error ? e.message : i18n.t('sync_ai_propose_failed')
  } finally {
    diagnosisProposing.value = false
  }
}

function classificationLabel(value?: string) {
  const known = ["site_side", "cookie", "rate_limit", "proxy", "config",
                 "removed_books", "unknown"]
  return known.includes(String(value)) ? i18n.t('sync_ai_class_' + value) : (value || "")
}

function classificationClass(value?: string) {
  if (value === "config" || value === "rate_limit") return "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300"
  if (value === "site_side" || value === "cookie" || value === "proxy") return "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300"
  return "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
}

// The store keeps polling the active task, so reload the diagnosis whenever the
// selected task changes (including the first render after a page load).
watch(() => activeTask.value?.id, (id) => {
  if (id) void loadDiagnosis(id)
})

function selectAllSources() {
  selectedSourceIds.value = syncableSources.value.map((s: any) => s.id)
}

function invertSources() {
  const selected = new Set(selectedSourceIds.value)
  selectedSourceIds.value = syncableSources.value
    .filter((s: any) => !selected.has(s.id))
    .map((s: any) => s.id)
}

function clearSources() {
  selectedSourceIds.value = []
}

async function loadAutoSyncSettings() {
  try {
    const res = await api.get<any>("/admin/settings/auto-sync")
    autoSyncEnabled.value = res.enabled
    autoSyncTime.value = res.time || "03:00"
    autoSyncIntervalHours.value = Number(res.interval_hours || 0)
  } catch {
    // Settings are admin-only; ignore for non-admin visitors.
  }
}

async function saveAutoSyncSettings() {
  autoSyncSaving.value = true
  autoSyncError.value = ""
  autoSyncSaved.value = false
  try {
    const res = await api.put<any>("/admin/settings/auto-sync", {
      enabled: autoSyncEnabled.value,
      time: autoSyncTime.value,
      interval_hours: Number(autoSyncIntervalHours.value || 0),
    })
    autoSyncEnabled.value = res.enabled
    autoSyncTime.value = res.time
    autoSyncIntervalHours.value = Number(res.interval_hours || 0)
    autoSyncSaved.value = true
  } catch (e) {
    autoSyncError.value = e instanceof Error ? e.message : i18n.t('sync_auto_save_failed')
  } finally {
    autoSyncSaving.value = false
  }
}

async function startCrawl() {
  pageError.value = ""
  if (selectedSourceIds.value.length === 0) {
    pageError.value = i18n.t('sync_please_select_source')
    return
  }
  starting.value = true
  try {
    const created: any[] = []
    for (const id of selectedSourceIds.value) {
      if (!syncableSources.value.some((s: any) => s.id === id)) continue
      const task = await api.post<any>("/crawl/tasks", {
        source: id,
        max_pages: Math.max(0, Math.floor(Number(syncMaxPages.value) || 0)),
      })
      created.push(task)
    }
    if (created.length > 0) {
      await crawlStore.setTask(created[0])
    }
    await loadTasks()
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : i18n.t('sync_failed_start')
  } finally {
    starting.value = false
  }
}

async function startBookshelfSync() {
  pageError.value = ""
  bookshelfResults.value = []
  if (selectedSourceIds.value.length === 0) {
    pageError.value = i18n.t('sync_please_select_source')
    return
  }
  bookshelfSyncing.value = true
  try {
    for (const id of selectedSourceIds.value) {
      if (!syncableSources.value.some((s: any) => s.id === id)) continue
      try {
        const result = await api.post<any>("/sync/bookshelf", { source_id: id })
        bookshelfResults.value.push({
          source_id: id,
          source_name: sourceName({ source: id }),
          ok: true,
          total: result.total || 0,
        })
      } catch (e) {
        bookshelfResults.value.push({
          source_id: id,
          source_name: sourceName({ source: id }),
          ok: false,
          error: e instanceof Error ? e.message : i18n.t('sync_failed_start'),
        })
      }
    }
  } finally {
    bookshelfSyncing.value = false
  }
}

async function taskAction(task: any, action: string) {
  pageError.value = ""
  try {
    const updated = await api.post<any>(`/crawl/tasks/${task.id}/${action}`)
    const merged = { ...task, ...updated }
    const idx = tasks.value.findIndex((t) => t.id === task.id)
    if (idx !== -1) tasks.value[idx] = merged
    if (crawlStore.activeTask?.id === task.id) {
      await crawlStore.setTask(merged)
    }
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : i18n.t('sync_failed_action')
  }
}

async function cancelTaskById(task: any) {
  if (!confirm(i18n.t('sync_cancel_confirm'))) return
  await taskAction(task, "cancel")
}

async function moveTaskFront(task: any) {
  await taskAction(task, "move-front")
}

async function pauseTask() {
  if (activeTask.value) await taskAction(activeTask.value, "pause")
}

async function resumeTask() {
  if (activeTask.value) await taskAction(activeTask.value, "resume")
}

async function cancelTask() {
  if (activeTask.value) await cancelTaskById(activeTask.value)
}

async function deleteTask(task: any) {
  if (!confirm(i18n.t('sync_delete_confirm'))) return
  pageError.value = ""
  historyNotice.value = ""
  try {
    await api.delete<any>(`/crawl/tasks/${task.id}`)
    tasks.value = tasks.value.filter((t) => t.id !== task.id)
    if (crawlStore.activeTask?.id === task.id) crawlStore.clear()
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : i18n.t('sync_delete_failed')
  }
}

async function clearHistory() {
  if (!confirm(i18n.t('sync_clear_history_confirm'))) return
  pageError.value = ""
  historyNotice.value = ""
  try {
    const res = await api.post<any>("/crawl/tasks/clear-history")
    historyNotice.value = i18n.t('sync_clear_history_done', { n: res.deleted ?? 0 })
    await loadTasks()
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : i18n.t('sync_clear_history_failed')
  }
}

onMounted(async () => {
  await loadSources()
  await loadAutoSyncSettings()
  await loadTasks()
  if (crawlStore.activeTask?.id && pollable.includes(crawlStore.activeTask.status)) {
    crawlStore.startPolling(crawlStore.activeTask.id)
  }
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-4xl mx-auto px-4 py-8">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-2xl font-bold">{{ i18n.t('sync_title') }}</h1>
          <p class="text-sm text-muted dark:text-gray-400 mt-1">
            {{ i18n.t('sync_subtitle') }}
          </p>
        </div>
        <button @click="loadTasks" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-surface transition-colors">
          {{ i18n.t('sync_refresh') }}
        </button>
      </div>

      <p v-if="pageError" class="text-sm text-red-600 mb-4">{{ pageError }}</p>

      <section v-if="auth.user" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mb-6">
        <h2 class="text-sm font-semibold mb-3">{{ auth.isAdmin ? i18n.t('sync_start_full') : i18n.t('sync_start_my') }}</h2>

        <div class="flex flex-wrap items-center gap-2 mb-3 text-xs">
          <button @click="selectAllSources" class="px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5">{{ i18n.t('sync_select_all') }}</button>
          <button @click="invertSources" class="px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5">{{ i18n.t('sync_select_invert') }}</button>
          <button @click="clearSources" class="px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5">{{ i18n.t('sync_select_none') }}</button>
          <span class="text-muted dark:text-gray-400">{{ i18n.t('sync_selected_count', { n: selectedSourceIds.length }) }} / {{ syncableSources.length }}</span>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 max-h-64 overflow-y-auto mb-4">
          <label
            v-for="s in syncableSources"
            :key="s.id"
            class="flex items-start gap-2 px-3 py-2 rounded border border-border dark:border-gray-700 bg-paper dark:bg-gray-800 cursor-pointer hover:bg-accent/5"
          >
            <input type="checkbox" :value="s.id" v-model="selectedSourceIds" class="mt-0.5 rounded" />
            <span class="min-w-0">
              <span class="block text-sm font-medium truncate">{{ s.name }}</span>
              <span class="block text-xs text-muted dark:text-gray-400 truncate">{{ s.id }}</span>
              <span
                v-if="s.sync_interval_seconds != null && s.sync_interval_seconds > 0"
                class="block text-xs text-amber-600 dark:text-amber-400 truncate"
              >{{ i18n.t('admin_sync_interval_badge', { seconds: s.sync_interval_seconds }) }}</span>
            </span>
          </label>
          <p v-if="syncableSources.length === 0" class="col-span-full text-sm text-muted dark:text-gray-400 py-4 text-center">{{ i18n.t('sync_no_sources') }}</p>
        </div>

        <div class="flex flex-wrap items-center gap-3 mb-1">
          <label class="inline-flex items-center gap-2 text-sm">
            <span class="text-muted dark:text-gray-400">{{ i18n.t('sync_max_pages') }}</span>
            <input
              type="number"
              min="0"
              v-model.number="syncMaxPages"
              class="w-24 px-2 py-1.5 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
            />
          </label>
          <button @click="startCrawl" :disabled="starting" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
            {{ starting ? i18n.t('sync_starting') : i18n.t('sync_import_books') }}
          </button>
          <button @click="startBookshelfSync" :disabled="bookshelfSyncing" class="px-4 py-2 rounded border border-accent text-accent text-sm font-medium hover:bg-accent/10 disabled:opacity-50">
            {{ bookshelfSyncing ? i18n.t('sync_starting') : i18n.t('sync_import_bookshelf') }}
          </button>
        </div>
        <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('sync_max_pages_hint') }}</p>
        <p v-if="throttledSources.length" class="text-xs text-amber-600 dark:text-amber-400 mb-3">{{ i18n.t('sync_interval_hint', { count: throttledSources.length }) }}</p>
        <div v-if="bookshelfResults.length" class="mt-3 space-y-1 text-xs">
          <p
            v-for="r in bookshelfResults"
            :key="r.source_id"
            :class="r.ok ? 'text-green-700 dark:text-green-400' : 'text-red-600'"
          >{{ r.source_name }}: {{ r.ok ? i18n.t('sync_bookshelf_done', { n: r.total }) : r.error }}</p>
        </div>

        <div v-if="auth.isAdmin" class="mt-5 pt-4 border-t border-border dark:border-gray-700">
          <h3 class="text-sm font-semibold mb-3">{{ i18n.t('sync_auto_title') }}</h3>
          <div class="flex flex-wrap items-center gap-3">
            <label class="inline-flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" v-model="autoSyncEnabled" class="rounded" />
              {{ i18n.t('sync_auto_enable') }}
            </label>
            <label class="inline-flex items-center gap-2 text-sm">
              <span class="text-muted dark:text-gray-400">{{ i18n.t('sync_auto_mode') }}</span>
              <select
                v-model.number="autoSyncIntervalHours"
                class="px-2 py-1.5 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
              >
                <option :value="0">{{ i18n.t('sync_auto_mode_daily') }}</option>
                <option v-for="hours in [1, 2, 3, 4, 6, 8, 12, 24]" :key="hours" :value="hours">
                  {{ i18n.t('sync_auto_every_hours', { n: hours }) }}
                </option>
              </select>
            </label>
            <label v-if="autoSyncIntervalHours === 0" class="inline-flex items-center gap-2 text-sm">
              <span class="text-muted dark:text-gray-400">{{ i18n.t('sync_auto_time') }}</span>
              <input
                type="time"
                v-model="autoSyncTime"
                class="px-2 py-1.5 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
              />
            </label>
            <button
              @click="saveAutoSyncSettings"
              :disabled="autoSyncSaving"
              class="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
            >{{ autoSyncSaving ? i18n.t('sync_auto_saving') : i18n.t('sync_auto_save') }}</button>
          </div>
          <p v-if="autoSyncError" class="text-xs text-red-600 mt-2">{{ autoSyncError }}</p>
          <p v-else-if="autoSyncSaved" class="text-xs text-green-600 mt-2">{{ i18n.t('sync_auto_saved') }}</p>
          <p class="text-xs text-muted dark:text-gray-400 mt-2">{{ i18n.t('sync_auto_hint') }}</p>
        </div>
      </section>

      <section v-if="activeTask" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mb-6">
        <div class="flex items-center justify-between mb-3">
          <div>
            <h2 class="text-sm font-semibold">{{ i18n.t('sync_selected_task') }}</h2>
            <p class="text-xs text-muted dark:text-gray-400 mt-1">{{ sourceName(activeTask) }} ({{ activeTask.id }})</p>
          </div>
          <span class="text-xs px-2 py-1 rounded-full" :class="activeTask.status === 'running' ? 'bg-blue-100 text-blue-700' : activeTask.status === 'paused' ? 'bg-amber-100 text-amber-700' : activeTask.status === 'completed_with_errors' ? 'bg-amber-100 text-amber-700' : activeTask.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600'">{{ statusText(activeTask.status) }}</span>
        </div>
        <div class="h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden mb-2">
          <div class="h-full bg-accent transition-all" :style="{ width: activeProgress + '%' }"></div>
        </div>
        <div class="flex items-center justify-between text-xs text-muted dark:text-gray-400 mb-3">
          <span v-if="activeTask.max_pages > 0">{{ i18n.t('sync_pages') }} {{ activeTask.progress?.pages_checked || 0 }} / {{ activeTask.max_pages }}</span>
          <span v-else>{{ i18n.t('sync_pages') }} {{ activeTask.progress?.pages_checked || 0 }} ({{ i18n.t('sync_unlimited') }})</span>
          <span>{{ i18n.t('sync_found') }} {{ activeTask.progress?.books_found || 0 }}</span>
          <span v-if="activeTask.priority !== undefined">{{ i18n.t('sync_priority') }} {{ activeTask.priority }}</span>
        </div>
        <div class="grid grid-cols-3 gap-2 text-center mb-4">
          <div class="p-2 rounded bg-green-50 dark:bg-green-950">
            <div class="text-sm font-semibold text-green-700 dark:text-green-400">{{ activeTask.progress?.books_synced || 0 }}</div>
            <div class="text-xs text-muted">{{ i18n.t('sync_synced') }}</div>
          </div>
          <div class="p-2 rounded bg-red-50 dark:bg-red-950">
            <div class="text-sm font-semibold text-red-700 dark:text-red-400">{{ activeTask.progress?.books_failed || 0 }}</div>
            <div class="text-xs text-muted">{{ i18n.t('sync_failed') }}</div>
          </div>
          <div class="p-2 rounded bg-blue-50 dark:bg-blue-950">
            <div class="text-sm font-semibold text-blue-700 dark:text-blue-400">{{ activeTask.progress?.chapters_created || 0 }}</div>
            <div class="text-xs text-muted">{{ i18n.t('sync_chapters') }}</div>
          </div>
        </div>
        <div class="flex gap-2">
          <button v-if="activeTask.status === 'running' || activeTask.status === 'pending'" @click="pauseTask" class="px-3 py-1.5 text-xs rounded border border-border dark:border-gray-700 hover:bg-accent/5">{{ i18n.t('sync_pause') }}</button>
          <button v-if="activeTask.status === 'paused'" @click="resumeTask" class="px-3 py-1.5 text-xs rounded border border-green-600 text-green-700 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-950">{{ i18n.t('sync_resume') }}</button>
          <button v-if="activeTask.status === 'pending' || activeTask.status === 'paused'" @click="moveTaskFront(activeTask)" class="px-3 py-1.5 text-xs rounded border border-accent text-accent hover:bg-accent/10">{{ i18n.t('sync_top') }}</button>
          <button v-if="!terminal.includes(activeTask.status)" @click="cancelTask" class="px-3 py-1.5 text-xs rounded border border-red-500 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950">{{ i18n.t('sync_cancel') }}</button>
          <button v-if="deletable.includes(activeTask.status)" @click="deleteTask(activeTask)" class="px-3 py-1.5 text-xs rounded border border-red-300 text-red-500 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950">{{ i18n.t('sync_delete') }}</button>
        </div>
        <p v-if="activeTask.error" class="text-xs text-red-600 mt-3">{{ activeTask.error }}</p>
        <p v-if="activeTask.result" class="text-xs text-muted dark:text-gray-400 mt-3">
          {{ i18n.t('sync_result_summary', {
            found: activeTask.result.books_found,
            synced: activeTask.result.books_synced,
            failed: activeTask.result.books_failed,
            chapters: activeTask.result.chapters_created,
          }) }}
        </p>

        <!-- AI diagnosis: explains the failure, proposes (never applies) changes -->
        <div v-if="canDiagnose && (diagnosisWorthwhile || diagnosis)" class="mt-4 pt-4 border-t border-border dark:border-gray-700">
          <div class="flex items-center justify-between gap-2 mb-2">
            <h3 class="text-sm font-semibold">{{ i18n.t('sync_ai_title') }}</h3>
            <button
              @click="runDiagnosis"
              :disabled="diagnosisLoading"
              class="px-3 py-1.5 text-xs rounded border border-accent text-accent hover:bg-accent/10 disabled:opacity-50 shrink-0"
            >{{ diagnosisLoading
                 ? i18n.t('sync_ai_running')
                 : (diagnosis ? i18n.t('sync_ai_rerun') : i18n.t('sync_ai_run')) }}</button>
          </div>
          <p class="text-[11px] text-muted dark:text-gray-400 mb-2">{{ i18n.t('sync_ai_hint') }}</p>

          <p v-if="diagnosisError" class="text-xs text-red-500 break-words">{{ diagnosisError }}</p>
          <p v-else-if="diagnosisNotice" class="text-xs text-green-600 dark:text-green-400">{{ diagnosisNotice }}</p>

          <div v-if="diagnosis" class="space-y-3">
            <div class="flex flex-wrap items-center gap-2">
              <span class="text-[11px] px-2 py-0.5 rounded-full" :class="classificationClass(diagnosis.classification)">
                {{ classificationLabel(diagnosis.classification) }}
              </span>
              <span class="text-[11px] text-muted dark:text-gray-400">
                {{ i18n.t('sync_ai_confidence', { level: diagnosis.confidence || '?' }) }}
                · {{ diagnosis.model }}
              </span>
            </div>

            <p class="text-sm text-ink whitespace-pre-wrap break-words">{{ diagnosis.summary }}</p>

            <div v-if="diagnosis.reasoning?.length">
              <p class="text-[11px] font-medium text-muted dark:text-gray-400 mb-1">{{ i18n.t('sync_ai_reasoning') }}</p>
              <ul class="list-disc pl-4 space-y-0.5">
                <li v-for="(line, i) in diagnosis.reasoning" :key="i" class="text-xs text-ink break-words">{{ line }}</li>
              </ul>
            </div>

            <div v-if="diagnosis.next_steps?.length">
              <p class="text-[11px] font-medium text-muted dark:text-gray-400 mb-1">{{ i18n.t('sync_ai_next') }}</p>
              <ol class="list-decimal pl-4 space-y-0.5">
                <li v-for="(line, i) in diagnosis.next_steps" :key="i" class="text-xs text-ink break-words">{{ line }}</li>
              </ol>
            </div>

            <div v-if="diagnosis.proposed_changes?.length">
              <p class="text-[11px] font-medium text-muted dark:text-gray-400 mb-1">
                {{ i18n.t('sync_ai_proposals', { n: diagnosis.proposed_changes.length }) }}
              </p>
              <div class="space-y-2">
                <div
                  v-for="(change, i) in diagnosis.proposed_changes"
                  :key="i"
                  class="p-2 rounded border border-border dark:border-gray-700 text-xs"
                >
                  <div class="flex items-center gap-2 mb-1">
                    <code class="text-accent break-all">{{ change.path }}</code>
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800">{{ change.risk }}</span>
                    <span
                      v-if="change.mismatch"
                      class="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300"
                    >{{ i18n.t('sync_ai_stale') }}</span>
                  </div>
                  <p class="text-muted dark:text-gray-400 break-words">{{ i18n.t('sync_ai_current') }}<code class="break-all">{{ change.current || '—' }}</code></p>
                  <p class="text-muted dark:text-gray-400 break-words">{{ i18n.t('sync_ai_suggest') }}<code class="break-all">{{ change.new_text ?? change.new }}</code></p>
                  <p v-if="change.reason" class="mt-1 text-muted dark:text-gray-400 break-words">{{ change.reason }}</p>
                </div>
              </div>
              <div class="flex flex-wrap items-center gap-2 mt-2">
                <button
                  v-if="!diagnosis.change_id"
                  @click="proposeSourceChange"
                  :disabled="diagnosisProposing"
                  class="px-3 py-1.5 text-xs rounded bg-accent text-white font-medium hover:opacity-90 disabled:opacity-50"
                >{{ diagnosisProposing ? i18n.t('sync_ai_proposing') : i18n.t('sync_ai_propose') }}</button>
                <span v-else class="text-xs text-green-600 dark:text-green-400">{{ i18n.t('sync_ai_proposed') }}</span>
              </div>
            </div>
            <p v-else class="text-[11px] text-muted dark:text-gray-400">{{ i18n.t('sync_ai_no_change') }}</p>
          </div>
        </div>
      </section>

      <section class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-semibold">{{ i18n.t('sync_recent_tasks') }}</h2>
          <div class="flex items-center gap-2">
            <span class="text-xs text-muted dark:text-gray-400">{{ i18n.t('sync_tasks_count', { n: tasks.length }) }}</span>
            <button
              v-if="tasks.some((t) => deletable.includes(t.status))"
              @click="clearHistory"
              class="text-xs px-2 py-1 rounded border border-red-300 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950"
            >{{ i18n.t('sync_clear_history') }}</button>
          </div>
        </div>
        <p v-if="historyNotice" class="text-xs text-green-600 dark:text-green-400 mb-3">{{ historyNotice }}</p>
        <p v-if="loadingTasks" class="text-sm text-muted">{{ i18n.t('sync_loading') }}</p>
        <p v-else-if="tasks.length === 0" class="text-sm text-muted">{{ i18n.t('sync_no_tasks') }}</p>
        <div v-else class="divide-y divide-border">
          <div
            v-for="task in orderedTasks"
            :key="task.id"
            class="py-3 cursor-pointer hover:bg-accent/5 transition-colors"
            @click="selectTask(task)"
          >
            <div class="flex items-center justify-between mb-1 gap-2">
              <span class="text-sm font-medium">{{ sourceName(task) }}</span>
              <div class="flex items-center gap-2 shrink-0">
                <span class="text-xs px-2 py-0.5 rounded-full" :class="task.status === 'completed' ? 'bg-green-100 text-green-700' : task.status === 'completed_with_errors' ? 'bg-amber-100 text-amber-700' : task.status === 'failed' || task.status === 'cancelled' ? 'bg-red-100 text-red-700' : 'bg-blue-100 text-blue-700'">{{ statusText(task.status) }}</span>
                <button v-if="task.status === 'running' || task.status === 'pending'" @click.stop="taskAction(task, 'pause')" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5">{{ i18n.t('sync_pause') }}</button>
                <button v-if="task.status === 'paused'" @click.stop="taskAction(task, 'resume')" class="text-xs px-2 py-1 rounded border border-green-600 text-green-700 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-950">{{ i18n.t('sync_resume') }}</button>
                <button v-if="task.status === 'pending' || task.status === 'paused'" @click.stop="moveTaskFront(task)" class="text-xs px-2 py-1 rounded border border-accent text-accent hover:bg-accent/10">{{ i18n.t('sync_top') }}</button>
                <button v-if="!terminal.includes(task.status)" @click.stop="cancelTaskById(task)" class="text-xs px-2 py-1 rounded border border-red-500 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950">{{ i18n.t('sync_cancel') }}</button>
                <button v-if="deletable.includes(task.status)" @click.stop="deleteTask(task)" class="text-xs px-2 py-1 rounded border border-red-300 text-red-500 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950">{{ i18n.t('sync_delete') }}</button>
              </div>
            </div>
            <div class="flex items-center gap-3 text-xs text-muted dark:text-gray-400">
              <span>{{ task.mode || i18n.t('sync_mode') }}</span>
              <span v-if="task.max_pages > 0">{{ i18n.t('sync_pages') }} {{ task.progress?.pages_checked || 0 }}/{{ task.max_pages }}</span>
              <span v-else>{{ i18n.t('sync_pages') }} {{ task.progress?.pages_checked || 0 }} ({{ i18n.t('sync_unlimited') }})</span>
              <span>{{ i18n.t('sync_books') }} {{ task.progress?.books_found || 0 }}</span>
              <span v-if="task.priority !== undefined">{{ i18n.t('sync_priority') }} {{ task.priority }}</span>
              <span v-if="task.finished_at">{{ i18n.t('sync_finished') }} {{ new Date(task.finished_at).toLocaleString() }}</span>
            </div>
            <div class="h-1 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden mt-2">
              <div class="h-full bg-accent" :style="{ width: Math.min(100, Math.round(((task.max_pages > 0 ? (task.progress?.pages_checked || 0) / task.max_pages : 0)) * 100)) + '%' }"></div>
            </div>
            <div v-if="task.error" class="mt-2 text-xs text-red-600 dark:text-red-400">{{ task.error }}</div>
            <details v-if="task.result?.details?.length" class="mt-2 text-xs" @click.stop>
              <summary class="cursor-pointer text-accent">同步书籍明细（{{ task.result.details.length }}）</summary>
              <div class="mt-2 space-y-1 pl-3 border-l border-border dark:border-gray-700">
                <div v-for="detail in task.result.details" :key="detail.url || detail.title" class="flex flex-wrap gap-x-2 gap-y-1">
                  <span :class="detail.filtered ? 'text-gray-500' : detail.synced === false || detail.failed_chapters?.length ? 'text-red-600 dark:text-red-400' : 'text-green-700 dark:text-green-400'">{{ detail.filtered ? '已过滤' : detail.synced === false ? '失败' : detail.failed_chapters?.length ? '部分失败' : '成功' }}</span>
                  <span>{{ detail.title || detail.name || detail.url }}</span>
                  <span v-if="detail.error" class="text-red-600 dark:text-red-400">{{ detail.error }}</span>
                  <span v-if="detail.filtered" class="text-gray-500">{{ detail.filter_type }}: {{ detail.filter_value }}</span>
                  <span v-if="detail.failed_chapters?.length" class="text-red-600 dark:text-red-400">章节失败 {{ detail.failed_chapters.length }}</span>
                </div>
              </div>
            </details>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>
