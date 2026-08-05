<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
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

const activeTask = computed(() => crawlStore.activeTask)

const activeProgress = computed(() => {
  const task = activeTask.value
  if (!task) return 0
  if (!task.max_pages) return 0
  const max = task.max_pages || 1
  const pages = task.progress?.pages_checked || 0
  return Math.min(100, Math.round((pages / max) * 100))
})

const terminal = ["completed", "failed", "cancelled", "completed_with_errors"]

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
    tasks.value = await api.get<any[]>("/crawl/tasks?limit=50")
    const active = tasks.value.find((t) => ["pending", "running", "paused"].includes(t.status))
    const selectedStillHere = crawlStore.activeTask?.id
      ? tasks.value.some((t) => t.id === crawlStore.activeTask.id)
      : false
    if (!selectedStillHere && active) {
      await crawlStore.setTask(active)
    } else if (!selectedStillHere) {
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
      const task = await api.post<any>("/crawl/tasks", {
        source: id,
        max_pages: 0,
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

onMounted(async () => {
  await loadSources()
  await loadTasks()
  if (crawlStore.activeTask?.id && !terminal.includes(crawlStore.activeTask.status)) {
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

      <section v-if="auth.isAdmin" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mb-6">
        <h2 class="text-sm font-semibold mb-3">{{ i18n.t('sync_start_full') }}</h2>
        <div class="flex flex-col sm:flex-row gap-3">
          <select
            v-model="selectedSourceIds"
            multiple
            size="6"
            class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
          >
            <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.name }} ({{ s.id }})</option>
          </select>
          <button @click="startCrawl" :disabled="starting" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
            {{ starting ? i18n.t('sync_starting') : i18n.t('sync_start') }}
          </button>
        </div>
      </section>

      <section v-if="activeTask" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mb-6">
        <div class="flex items-center justify-between mb-3">
          <div>
            <h2 class="text-sm font-semibold">{{ i18n.t('sync_selected_task') }}</h2>
            <p class="text-xs text-muted dark:text-gray-400 mt-1">{{ sourceName(activeTask) }} ({{ activeTask.id }})</p>
          </div>
          <span class="text-xs px-2 py-1 rounded-full" :class="activeTask.status === 'running' ? 'bg-blue-100 text-blue-700' : activeTask.status === 'paused' ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600'">{{ statusText(activeTask.status) }}</span>
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
      </section>

      <section class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-semibold">{{ i18n.t('sync_recent_tasks') }}</h2>
          <span class="text-xs text-muted dark:text-gray-400">{{ i18n.t('sync_tasks_count', { n: tasks.length }) }}</span>
        </div>
        <p v-if="loadingTasks" class="text-sm text-muted">{{ i18n.t('sync_loading') }}</p>
        <p v-else-if="tasks.length === 0" class="text-sm text-muted">{{ i18n.t('sync_no_tasks') }}</p>
        <div v-else class="divide-y divide-border">
          <div
            v-for="task in tasks"
            :key="task.id"
            class="py-3 cursor-pointer hover:bg-accent/5 transition-colors"
            @click="selectTask(task)"
          >
            <div class="flex items-center justify-between mb-1 gap-2">
              <span class="text-sm font-medium">{{ sourceName(task) }}</span>
              <div class="flex items-center gap-2 shrink-0">
                <span class="text-xs px-2 py-0.5 rounded-full" :class="task.status === 'completed' ? 'bg-green-100 text-green-700' : task.status === 'failed' || task.status === 'cancelled' ? 'bg-red-100 text-red-700' : 'bg-blue-100 text-blue-700'">{{ statusText(task.status) }}</span>
                <button v-if="task.status === 'running' || task.status === 'pending'" @click.stop="taskAction(task, 'pause')" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5">{{ i18n.t('sync_pause') }}</button>
                <button v-if="task.status === 'paused'" @click.stop="taskAction(task, 'resume')" class="text-xs px-2 py-1 rounded border border-green-600 text-green-700 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-950">{{ i18n.t('sync_resume') }}</button>
                <button v-if="task.status === 'pending' || task.status === 'paused'" @click.stop="moveTaskFront(task)" class="text-xs px-2 py-1 rounded border border-accent text-accent hover:bg-accent/10">{{ i18n.t('sync_top') }}</button>
                <button v-if="!terminal.includes(task.status)" @click.stop="cancelTaskById(task)" class="text-xs px-2 py-1 rounded border border-red-500 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950">{{ i18n.t('sync_cancel') }}</button>
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
          </div>
        </div>
      </section>
    </main>
  </div>
</template>
