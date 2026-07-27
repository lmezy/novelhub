<script setup lang="ts">
import { onMounted, ref } from "vue"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"

interface Source {
  id: string
  name: string
  url: string | null
  plugin_name: string
  enabled: boolean
}

interface CookieItem {
  id: string
  source: string
  cookie_data: string
  expired_at: string | null
}

const tab = ref<"sources" | "cookies" | "sync" | "logs">("sources")

// Sources
const sources = ref<Source[]>([])
const sourceForm = ref({ id: "", name: "", url: "", plugin_name: "alicesw" })
const sourceError = ref("")

async function loadSources() {
  sources.value = await api.get<Source[]>("/sources")
}

async function createSource() {
  sourceError.value = ""
  try {
    await api.post("/sources", sourceForm.value)
    await loadSources()
    sourceForm.value = { id: "", name: "", url: "", plugin_name: "alicesw" }
  } catch (e) {
    sourceError.value = e instanceof Error ? e.message : "Failed"
  }
}

// Cookies
const cookies = ref<CookieItem[]>([])
const cookieForm = ref({ source: "", cookie_data: "", expired_at: "" })
const cookieError = ref("")

async function loadCookies() {
  cookies.value = await api.get<CookieItem[]>("/cookies")
}

async function createCookie() {
  cookieError.value = ""
  try {
    const body: any = { source: cookieForm.value.source, cookie_data: cookieForm.value.cookie_data }
    if (cookieForm.value.expired_at) body.expired_at = cookieForm.value.expired_at
    await api.post("/cookies", body)
    await loadCookies()
    cookieForm.value = { source: "", cookie_data: "", expired_at: "" }
  } catch (e) {
    cookieError.value = e instanceof Error ? e.message : "Failed"
  }
}

async function deleteCookie(id: string) {
  await api.delete(`/cookies/${id}`)
  await loadCookies()
}

// Sync
const syncSourceId = ref("")
const syncUrl = ref("")
const syncResult = ref<any>(null)
const syncError = ref("")
const syncing = ref(false)

async function triggerSync() {
  syncError.value = ""
  syncResult.value = null
  syncing.value = true
  try {
    syncResult.value = await api.post("/sync/book", {
      source_id: syncSourceId.value,
      url: syncUrl.value,
    })
  } catch (e) {
    syncError.value = e instanceof Error ? e.message : "Sync failed"
  } finally {
    syncing.value = false
  }
}

// Logs
const logs = ref<any[]>([])

async function loadLogs() {
  logs.value = await api.get<any[]>("/crawl/tasks")
}

onMounted(async () => {
  await loadSources()
  await loadCookies()
})
</script>

<template>
  <div class="min-h-screen bg-paper">
    <NavBar />

    <main class="max-w-4xl mx-auto px-4 py-8">
      <h1 class="text-2xl font-bold mb-6">Admin</h1>

      <!-- Tabs -->
      <div class="flex gap-1 mb-8 border-b border-border">
        <button
          v-for="t in (['sources', 'cookies', 'sync', 'logs'] as const)"
          :key="t"
          @click="tab = t"
          class="px-4 py-2 text-sm transition-colors -mb-px"
          :class="tab === t
            ? 'border-b-2 border-accent text-accent font-medium'
            : 'text-muted hover:text-ink'"
        >{{ t === 'sources' ? 'Sources' : t === 'cookies' ? 'Cookies' : t === 'sync' ? 'Sync' : 'Logs' }}</button>
      </div>

      <!-- Sources Tab -->
      <section v-if="tab === 'sources'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border bg-surface">
          <h2 class="text-sm font-semibold mb-4">Add Source</h2>
          <div class="grid grid-cols-2 gap-3 mb-3">
            <input v-model="sourceForm.id" placeholder="Source ID (e.g. alicesw)" class="px-3 py-2 rounded border border-border text-sm bg-paper" />
            <input v-model="sourceForm.name" placeholder="Display name" class="px-3 py-2 rounded border border-border text-sm bg-paper" />
            <input v-model="sourceForm.url" placeholder="Base URL" class="px-3 py-2 rounded border border-border text-sm bg-paper" />
            <input v-model="sourceForm.plugin_name" placeholder="Plugin name" class="px-3 py-2 rounded border border-border text-sm bg-paper" />
          </div>
          <p v-if="sourceError" class="text-sm text-red-600 mb-2">{{ sourceError }}</p>
          <button @click="createSource" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">Create Source</button>
        </div>

        <div class="divide-y divide-border border border-border rounded-lg bg-surface">
          <div v-for="s in sources" :key="s.id" class="px-4 py-3 flex items-center justify-between">
            <div>
              <span class="text-sm font-medium">{{ s.name }}</span>
              <span class="text-xs text-muted ml-2">{{ s.id }} ({{ s.plugin_name }})</span>
            </div>
            <span class="text-xs" :class="s.enabled ? 'text-green-600' : 'text-red-500'">{{ s.enabled ? 'enabled' : 'disabled' }}</span>
          </div>
          <p v-if="sources.length === 0" class="px-4 py-3 text-sm text-muted">No sources configured.</p>
        </div>
      </section>

      <!-- Cookies Tab -->
      <section v-if="tab === 'cookies'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border bg-surface">
          <h2 class="text-sm font-semibold mb-4">Add Cookie</h2>
          <div class="space-y-3 mb-3">
            <input v-model="cookieForm.source" placeholder="Source ID" class="w-full px-3 py-2 rounded border border-border text-sm bg-paper" />
            <input v-model="cookieForm.expired_at" type="datetime-local" class="w-full px-3 py-2 rounded border border-border text-sm bg-paper" />
            <textarea v-model="cookieForm.cookie_data" placeholder="Cookie string..." rows="3" class="w-full px-3 py-2 rounded border border-border text-sm bg-paper resize-y" />
          </div>
          <p v-if="cookieError" class="text-sm text-red-600 mb-2">{{ cookieError }}</p>
          <button @click="createCookie" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">Save Cookie</button>
        </div>

        <div class="divide-y divide-border border border-border rounded-lg bg-surface">
          <div v-for="c in cookies" :key="c.id" class="px-4 py-3 flex items-center justify-between">
            <div>
              <span class="text-sm font-medium">{{ c.source }}</span>
              <span v-if="c.expired_at" class="text-xs text-muted ml-2">Expires: {{ new Date(c.expired_at).toLocaleDateString() }}</span>
            </div>
            <button @click="deleteCookie(c.id)" class="text-xs text-red-500 hover:text-red-700">Delete</button>
          </div>
          <p v-if="cookies.length === 0" class="px-4 py-3 text-sm text-muted">No cookies stored.</p>
        </div>
      </section>

      <!-- Sync Tab -->
      <section v-if="tab === 'sync'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border bg-surface">
          <h2 class="text-sm font-semibold mb-4">Trigger Sync</h2>
          <div class="flex gap-3 mb-3">
            <input v-model="syncSourceId" placeholder="Source ID" class="flex-1 px-3 py-2 rounded border border-border text-sm bg-paper" />
            <input v-model="syncUrl" placeholder="Book URL to sync" class="flex-1 px-3 py-2 rounded border border-border text-sm bg-paper" />
          </div>
          <p v-if="syncError" class="text-sm text-red-600 mb-2">{{ syncError }}</p>
          <button @click="triggerSync" :disabled="syncing" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
            {{ syncing ? 'Syncing...' : 'Sync Book' }}
          </button>
          <div v-if="syncResult" class="mt-4 p-3 rounded bg-green-50 text-sm">
            <p>Book ID: {{ syncResult.book_id }}</p>
            <p>Created chapters: {{ syncResult.created_chapters }} / Skipped: {{ syncResult.skipped_chapters }}</p>
          </div>
        </div>
      </section>

      <!-- Logs Tab -->
      <section v-if="tab === 'logs'" class="space-y-6">
        <button @click="loadLogs" class="px-4 py-2 rounded border border-border text-sm hover:bg-surface transition-colors mb-4">Refresh</button>

        <div class="divide-y divide-border border border-border rounded-lg bg-surface">
          <div v-for="log in logs" :key="log.id" class="px-4 py-3">
            <div class="flex items-center gap-2 mb-1">
              <span class="text-sm font-medium">{{ log.source }}</span>
              <span class="text-xs px-1.5 py-0.5 rounded-full" :class="log.status === 'completed' ? 'bg-green-100 text-green-700' : log.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-yellow-100 text-yellow-700'">{{ log.status }}</span>
            </div>
            <p class="text-xs text-muted">
              Started: {{ log.started_at ? new Date(log.started_at).toLocaleString() : '-' }}
              &middot; Finished: {{ log.finished_at ? new Date(log.finished_at).toLocaleString() : '-' }}
            </p>
            <p v-if="log.error" class="text-xs text-red-600 mt-1">{{ log.error }}</p>
          </div>
          <p v-if="logs.length === 0" class="px-4 py-3 text-sm text-muted">No crawl logs yet.</p>
        </div>
      </section>
    </main>
  </div>
</template>
const bookshelfSourceId = ref("")
const bookshelfResult = ref<any>(null)
const bookshelfError = ref("")
const bookshelfLoading = ref(false)

async function triggerBookshelfSync() {
  bookshelfError.value = ""
  bookshelfResult.value = null
  bookshelfLoading.value = true
  try {
    bookshelfResult.value = await api.post("/sync/bookshelf", {
      source_id: bookshelfSourceId.value,
    })
  } catch (e) {
    bookshelfError.value = e instanceof Error ? e.message : "Sync failed"
  } finally {
    bookshelfLoading.value = false
  }
}

        <div class="p-5 rounded-lg border border-border bg-surface mt-4">
          <h2 class="text-sm font-semibold mb-4">Bookshelf Sync</h2>
          <p class="text-xs text-muted mb-3">
            Sync all books from a user's bookshelf. Requires a saved cookie for the source.
          </p>
          <div class="flex gap-3 mb-3">
            <input v-model="bookshelfSourceId" placeholder="Source ID" class="flex-1 px-3 py-2 rounded border border-border text-sm bg-paper" />
          </div>
          <p v-if="bookshelfError" class="text-sm text-red-600 mb-2">{{ bookshelfError }}</p>
          <button @click="triggerBookshelfSync" :disabled="bookshelfLoading" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
            {{ bookshelfLoading ? 'Syncing...' : 'Sync Bookshelf' }}
          </button>
          <div v-if="bookshelfResult" class="mt-4 p-3 rounded bg-green-50 text-sm">
            <p>Source: {{ bookshelfResult.source_id }}</p>
            <p>Total books on shelf: {{ bookshelfResult.total }}</p>
            <div v-for="(r, i) in bookshelfResult.results" :key="i" class="mt-2 text-xs">
              <span :class="r.status === 'ok' ? 'text-green-700' : 'text-red-600'">
                {{ r.status === 'ok' ? 'OK' : 'Failed' }}: {{ r.book_id || r.url }}
              </span>
              <span v-if="r.error" class="text-red-500 ml-1">{{ r.error }}</span>
            </div>
          </div>
        </div>
