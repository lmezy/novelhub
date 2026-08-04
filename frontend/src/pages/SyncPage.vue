<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { api } from "../api/client"
import { useAuthStore } from "../stores/auth"
import { useCrawlStore } from "../stores/crawl"
import NavBar from "../components/NavBar.vue"

const auth = useAuthStore()
const crawlStore = useCrawlStore()
const sources = ref<any[]>([])
const tasks = ref<any[]>([])
const sourceId = ref("")
const starting = ref(false)
const loadingTasks = ref(false)
const pageError = ref("")

const activeTask = computed(() => crawlStore.activeTask)

const activeProgress = computed(() => {
  const task = activeTask.value
  if (!task) return 0
  const max = task.max_pages || 1
  const pages = task.progress?.pages_checked || 0
  return Math.min(100, Math.round((pages / max) * 100))
})

const terminal = ["completed", "failed", "cancelled", "completed_with_errors"]

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
    tasks.value = await api.get<any[]>("/crawl/tasks?limit=30")
    const active = tasks.value.find((t) => ["pending", "running", "paused"].includes(t.status))
    if (active && (!crawlStore.activeTask || crawlStore.activeTask.id !== active.id)) {
      await crawlStore.setTask(active)
    }
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : "加载任务失败"
  } finally {
    loadingTasks.value = false
  }
}

async function startCrawl() {
  pageError.value = ""
  if (!sourceId.value) {
    pageError.value = "请选择书源"
    return
  }
  starting.value = true
  try {
    const task = await api.post<any>("/crawl/tasks", {
      source: sourceId.value,
      max_pages: 500,
    })
    await crawlStore.setTask(task)
    await loadTasks()
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : "启动失败"
  } finally {
    starting.value = false
  }
}

async function pauseTask() {
  try {
    await crawlStore.pauseTask()
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : "暂停失败"
  }
}

async function resumeTask() {
  try {
    await crawlStore.resumeTask()
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : "恢复失败"
  }
}

async function cancelTask() {
  if (!confirm("确定取消当前同步任务吗？")) return
  try {
    await crawlStore.cancelTask()
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : "取消失败"
  }
}

function statusText(status: string) {
  const map: Record<string, string> = {
    pending: "等待中",
    running: "同步中",
    paused: "已暂停",
    completed: "已完成",
    completed_with_errors: "有错误完成",
    failed: "失败",
    cancelled: "已取消",
  }
  return map[status] || status
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
          <h1 class="text-2xl font-bold">同步进度</h1>
          <p class="text-sm text-muted dark:text-gray-400 mt-1">集中查看全站同步和书源导入任务</p>
        </div>
        <button @click="loadTasks" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-surface transition-colors">刷新</button>
      </div>

      <p v-if="pageError" class="text-sm text-red-600 mb-4">{{ pageError }}</p>

      <section v-if="auth.isAdmin" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mb-6">
        <h2 class="text-sm font-semibold mb-3">发起全站同步</h2>
        <div class="flex flex-col sm:flex-row gap-3">
          <select v-model="sourceId" class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
            <option value="" disabled>选择书源</option>
            <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.name }} ({{ s.id }})</option>
          </select>
          <button @click="startCrawl" :disabled="starting" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
            {{ starting ? '启动中...' : '开始同步' }}
          </button>
        </div>
      </section>

      <section v-if="activeTask" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mb-6">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-semibold">当前任务</h2>
          <span class="text-xs px-2 py-1 rounded-full" :class="activeTask.status === 'running' ? 'bg-blue-100 text-blue-700' : activeTask.status === 'paused' ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600'">{{ statusText(activeTask.status) }}</span>
        </div>
        <p class="text-xs text-muted dark:text-gray-400 mb-3">任务 {{ activeTask.id }}</p>
        <div class="h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden mb-2">
          <div class="h-full bg-accent transition-all" :style="{ width: activeProgress + '%' }"></div>
        </div>
        <div class="flex items-center justify-between text-xs text-muted dark:text-gray-400 mb-3">
          <span>已检查 {{ activeTask.progress?.pages_checked || 0 }} 页 / {{ activeTask.max_pages || 1 }} 页</span>
          <span>发现 {{ activeTask.progress?.books_found || 0 }} 本</span>
        </div>
        <div class="grid grid-cols-3 gap-2 text-center mb-4">
          <div class="p-2 rounded bg-green-50 dark:bg-green-950">
            <div class="text-sm font-semibold text-green-700 dark:text-green-400">{{ activeTask.progress?.books_synced || 0 }}</div>
            <div class="text-xs text-muted">已同步</div>
          </div>
          <div class="p-2 rounded bg-red-50 dark:bg-red-950">
            <div class="text-sm font-semibold text-red-700 dark:text-red-400">{{ activeTask.progress?.books_failed || 0 }}</div>
            <div class="text-xs text-muted">失败</div>
          </div>
          <div class="p-2 rounded bg-blue-50 dark:bg-blue-950">
            <div class="text-sm font-semibold text-blue-700 dark:text-blue-400">{{ activeTask.progress?.chapters_created || 0 }}</div>
            <div class="text-xs text-muted">新章节</div>
          </div>
        </div>
        <div class="flex gap-2">
          <button v-if="activeTask.status === 'running'" @click="pauseTask" class="px-3 py-1.5 text-xs rounded border border-border dark:border-gray-700 hover:bg-accent/5">暂停</button>
          <button v-if="activeTask.status === 'paused'" @click="resumeTask" class="px-3 py-1.5 text-xs rounded border border-green-600 text-green-700 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-950">继续</button>
          <button v-if="!terminal.includes(activeTask.status)" @click="cancelTask" class="px-3 py-1.5 text-xs rounded border border-red-500 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950">取消</button>
        </div>
        <p v-if="activeTask.error" class="text-xs text-red-600 mt-3">{{ activeTask.error }}</p>
        <p v-if="activeTask.result" class="text-xs text-muted dark:text-gray-400 mt-3">发现 {{ activeTask.result.books_found }} 本，成功 {{ activeTask.result.books_synced }} 本，失败 {{ activeTask.result.books_failed }} 本，新增章节 {{ activeTask.result.chapters_created }}</p>
      </section>

      <section class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-semibold">最近任务</h2>
          <span class="text-xs text-muted dark:text-gray-400">{{ tasks.length }} 条</span>
        </div>
        <p v-if="loadingTasks" class="text-sm text-muted">加载中...</p>
        <p v-else-if="tasks.length === 0" class="text-sm text-muted">暂无任务</p>
        <div v-else class="divide-y divide-border">
          <div v-for="task in tasks" :key="task.id" class="py-3">
            <div class="flex items-center justify-between mb-1">
              <span class="text-sm font-medium">{{ task.source }}</span>
              <span class="text-xs px-2 py-0.5 rounded-full" :class="task.status === 'completed' ? 'bg-green-100 text-green-700' : task.status === 'failed' || task.status === 'cancelled' ? 'bg-red-100 text-red-700' : 'bg-blue-100 text-blue-700'">{{ statusText(task.status) }}</span>
            </div>
            <div class="flex items-center gap-3 text-xs text-muted dark:text-gray-400">
              <span>{{ task.mode || 'bookshelf' }}</span>
              <span>页 {{ task.progress?.pages_checked || 0 }}/{{ task.max_pages || 200 }}</span>
              <span>书 {{ task.progress?.books_found || 0 }}</span>
              <span v-if="task.finished_at">完成 {{ new Date(task.finished_at).toLocaleString() }}</span>
            </div>
            <div class="h-1 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden mt-2">
              <div class="h-full bg-accent" :style="{ width: Math.min(100, Math.round(((task.progress?.pages_checked || 0) / (task.max_pages || 1)) * 100)) + '%' }"></div>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>
