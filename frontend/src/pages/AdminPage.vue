<script setup lang="ts">
import { onMounted, ref } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "../stores/i18n"
import NavBar from "../components/NavBar.vue"

const i18n = useI18nStore()

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

const tab = ref<"sources" | "cookies" | "sync" | "logs" | "tokens" | "status">("sources")

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
  await api.delete("/cookies/" + id)
  await loadCookies()
}

const syncSourceId = ref("")
const syncUrl = ref("")
const syncResult = ref<any>(null)
const syncError = ref("")
const syncing = ref(false)

const bookshelfSourceId = ref("")
const bookshelfResult = ref<any>(null)
const bookshelfError = ref("")
const bookshelfLoading = ref(false)

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

const logs = ref<any[]>([])

const tokens = ref<any[]>([])
const tokenName = ref("")
const tokenExpires = ref<number | null>(null)
const newToken = ref<string | null>(null)
const tokenError = ref("")

async function loadTokens() {
  try {
    tokens.value = await api.get<any[]>("/tokens")
  } catch { tokens.value = [] }
}

async function createToken() {
  tokenError.value = ""
  newToken.value = null
  try {
    const body: any = { name: tokenName.value }
    if (tokenExpires.value) body.expires_days = tokenExpires.value
    const res = await api.post<{ id: string; token: string; prefix: string }>("/tokens", body)
    newToken.value = res.token
    tokenName.value = ""
    tokenExpires.value = null
    await loadTokens()
  } catch (e) {
    tokenError.value = e instanceof Error ? e.message : "Failed"
  }
}

async function revokeToken(id: string) {
  await api.delete("/tokens/" + id)
  await loadTokens()
}

const healthStatus = ref<any>(null)

async function loadStatus() {
  try {
    healthStatus.value = await api.get<any>("/health/detailed")
  } catch { healthStatus.value = null }
}

async function loadLogs() {
  logs.value = await api.get<any[]>("/crawl/tasks")
}

onMounted(async () => {
  await loadSources()
  await loadCookies()
  await loadTokens()
  await loadStatus()
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-4xl mx-auto px-4 py-8">
      <h1 class="text-2xl font-bold mb-6">{{ i18n.t('admin_title') }}</h1>

      <div class="flex gap-1 mb-8 border-b border-border flex-wrap">
        <button
          v-for="t in (['sources', 'cookies', 'sync', 'logs', 'tokens', 'status'] as const)"
          :key="t"
          @click="tab = t"
          class="px-4 py-2 text-sm transition-colors -mb-px"
          :class="tab === t
            ? 'border-b-2 border-accent text-accent font-medium'
            : 'text-muted dark:text-gray-400 hover:text-ink'"
        >{{ i18n.t('admin_tab_' + t) }}</button>
      </div>

      <section v-if="tab === 'sources'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_add_source') }}</h2>
          <div class="grid grid-cols-2 gap-3 mb-3">
            <input v-model="sourceForm.id" :placeholder="i18n.t('admin_placeholder_id')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="sourceForm.name" :placeholder="i18n.t('admin_placeholder_name')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="sourceForm.url" :placeholder="i18n.t('admin_placeholder_url')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="sourceForm.plugin_name" :placeholder="i18n.t('admin_placeholder_plugin')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <p v-if="sourceError" class="text-sm text-red-600 mb-2">{{ sourceError }}</p>
          <button @click="createSource" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">{{ i18n.t('admin_create_source') }}</button>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="s in sources" :key="s.id" class="px-4 py-3 flex items-center justify-between">
            <div>
              <span class="text-sm font-medium">{{ s.name }}</span>
              <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ s.id }} ({{ s.plugin_name }})</span>
            </div>
            <span class="text-xs" :class="s.enabled ? 'text-green-600' : 'text-red-500'">{{ s.enabled ? i18n.t('admin_enabled') : i18n.t('admin_disabled') }}</span>
          </div>
          <p v-if="sources.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_sources') }}</p>
        </div>
      </section>

      <section v-if="tab === 'cookies'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_add_cookie') }}</h2>
          <div class="space-y-3 mb-3">
            <input v-model="cookieForm.source" :placeholder="i18n.t('admin_placeholder_source')" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="cookieForm.expired_at" type="datetime-local" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <textarea v-model="cookieForm.cookie_data" :placeholder="i18n.t('admin_placeholder_cookie')" rows="3" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y" />
          </div>
          <p v-if="cookieError" class="text-sm text-red-600 mb-2">{{ cookieError }}</p>
          <button @click="createCookie" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">{{ i18n.t('admin_save_cookie') }}</button>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="c in cookies" :key="c.id" class="px-4 py-3 flex items-center justify-between">
            <div>
              <span class="text-sm font-medium">{{ c.source }}</span>
              <span v-if="c.expired_at" class="text-xs text-muted dark:text-gray-400 ml-2">{{ i18n.t('admin_expires') }}: {{ new Date(c.expired_at).toLocaleDateString() }}</span>
            </div>
            <button @click="deleteCookie(c.id)" class="text-xs text-red-500 hover:text-red-700">{{ i18n.t('admin_delete') }}</button>
          </div>
          <p v-if="cookies.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_cookies') }}</p>
        </div>
      </section>

      <section v-if="tab === 'sync'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_trigger_sync') }}</h2>
          <div class="flex gap-3 mb-3">
            <input v-model="syncSourceId" :placeholder="i18n.t('admin_placeholder_source')" class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="syncUrl" :placeholder="i18n.t('admin_placeholder_book_url')" class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <p v-if="syncError" class="text-sm text-red-600 mb-2">{{ syncError }}</p>
          <button @click="triggerSync" :disabled="syncing" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
            {{ syncing ? i18n.t('admin_syncing') : i18n.t('admin_sync_book') }}
          </button>
          <div v-if="syncResult" class="mt-4 p-3 rounded bg-green-50 text-sm">
            <p>{{ i18n.t('admin_book_id') }}: {{ syncResult.book_id }}</p>
            <p>{{ i18n.t('admin_created_chapters') }}: {{ syncResult.created_chapters }} / {{ i18n.t('admin_skipped') }}: {{ syncResult.skipped_chapters }}</p>
          </div>
        </div>

        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mt-4">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_bookshelf_sync') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_bookshelf_hint') }}</p>
          <div class="flex gap-3 mb-3">
            <input v-model="bookshelfSourceId" :placeholder="i18n.t('admin_placeholder_source')" class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <p v-if="bookshelfError" class="text-sm text-red-600 mb-2">{{ bookshelfError }}</p>
          <button @click="triggerBookshelfSync" :disabled="bookshelfLoading" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
            {{ bookshelfLoading ? i18n.t('admin_syncing') : i18n.t('admin_sync_bookshelf') }}
          </button>
          <div v-if="bookshelfResult" class="mt-4 p-3 rounded bg-green-50 text-sm">
            <p>Source: {{ bookshelfResult.source_id }}</p>
            <p>{{ i18n.t('admin_total') }}: {{ bookshelfResult.total }}</p>
            <div v-for="(r, i) in bookshelfResult.results" :key="i" class="mt-2 text-xs">
              <span :class="r.status === 'ok' ? 'text-green-700' : 'text-red-600'">
                {{ r.status === 'ok' ? 'OK' : 'Failed' }}: {{ r.book_id || r.url }}
              </span>
              <span v-if="r.error" class="text-red-500 ml-1">{{ r.error }}</span>
            </div>
          </div>
        </div>
      </section>

      <section v-if="tab === 'logs'" class="space-y-6">
        <button @click="loadLogs" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-surface dark:bg-gray-900 transition-colors mb-4">{{ i18n.t('admin_refresh') }}</button>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="log in logs" :key="log.id" class="px-4 py-3">
            <div class="flex items-center gap-2 mb-1">
              <span class="text-sm font-medium">{{ log.source }}</span>
              <span class="text-xs px-1.5 py-0.5 rounded-full" :class="log.status === 'completed' ? 'bg-green-100 text-green-700' : log.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-yellow-100 text-yellow-700'">{{ log.status }}</span>
            </div>
            <p class="text-xs text-muted dark:text-gray-400">
              {{ i18n.t('admin_started') }}: {{ log.started_at ? new Date(log.started_at).toLocaleString() : '-' }}
              &middot; {{ i18n.t('admin_finished') }}: {{ log.finished_at ? new Date(log.finished_at).toLocaleString() : '-' }}
            </p>
            <p v-if="log.error" class="text-xs text-red-600 mt-1">{{ log.error }}</p>
          </div>
          <p v-if="logs.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_logs') }}</p>
        </div>
      </section>

      <section v-if="tab === 'status'" class="space-y-4">
        <button @click="loadStatus" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-surface dark:bg-gray-900 transition-colors mb-4">{{ i18n.t('admin_refresh') }}</button>
        <div v-if="healthStatus" class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div v-for="(val, key) in healthStatus.checks" :key="key" class="p-4 rounded-lg border" :class="val === 'ok' ? 'border-green-200 bg-green-50' : typeof val === 'object' ? 'border-border bg-surface dark:bg-gray-900' : 'border-red-200 bg-red-50'">
            <div class="flex items-center gap-2 mb-1">
              <span class="text-xs font-medium uppercase text-muted dark:text-gray-400">{{ key }}</span>
              <span class="w-2 h-2 rounded-full" :class="val === 'ok' ? 'bg-green-500' : typeof val === 'object' ? 'bg-blue-500' : 'bg-red-500'"></span>
            </div>
            <template v-if="typeof val === 'object'">
              <p class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_total') }}: {{ val.total_gb }} GB</p>
              <p class="text-xs text-muted dark:text-gray-400">Used: {{ val.used_gb }} GB</p>
              <p class="text-xs text-muted dark:text-gray-400">Free: {{ val.free_gb }} GB</p>
            </template>
            <p v-else class="text-sm" :class="val === 'ok' ? 'text-green-700' : 'text-red-600'">{{ val }}</p>
          </div>
        </div>
      </section>

      <section v-if="tab === 'tokens'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_create_token') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_token_hint') }}</p>
          <div class="flex gap-3 mb-3">
            <input v-model="tokenName" :placeholder="i18n.t('admin_placeholder_token_name')" class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model.number="tokenExpires" type="number" :placeholder="i18n.t('admin_placeholder_days')" class="w-32 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" min="1" max="365" />
            <button @click="createToken" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 shrink-0">{{ i18n.t('admin_create') }}</button>
          </div>
          <p v-if="tokenError" class="text-xs text-red-600 mb-2">{{ tokenError }}</p>
          <div v-if="newToken" class="mt-3 p-3 rounded bg-green-50 text-sm">
            <p class="font-medium mb-1">{{ i18n.t('admin_new_token') }}</p>
            <code class="text-xs break-all bg-green-100 px-2 py-1 rounded">{{ newToken }}</code>
          </div>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="t in tokens" :key="t.id" class="px-4 py-3 flex items-center justify-between">
            <div>
              <span class="text-sm font-medium">{{ t.name }}</span>
              <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ t.prefix }}...</span>
              <span v-if="t.last_used_at" class="text-xs text-muted dark:text-gray-400 ml-2">{{ i18n.t('admin_last') }}: {{ new Date(t.last_used_at).toLocaleDateString() }}</span>
            </div>
            <div class="flex items-center gap-3">
              <span class="text-xs" :class="t.is_active ? 'text-green-600' : 'text-red-500'">{{ t.is_active ? i18n.t('admin_active') : i18n.t('admin_revoked') }}</span>
              <button v-if="t.is_active" @click="revokeToken(t.id)" class="text-xs text-red-500 hover:text-red-700">{{ i18n.t('admin_revoke') }}</button>
            </div>
          </div>
          <p v-if="tokens.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_tokens') }}</p>
        </div>
      </section>
    </main>
  </div>
</template>