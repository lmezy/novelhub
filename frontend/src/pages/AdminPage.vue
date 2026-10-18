<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import { api } from "../api/client"
import { useI18nStore } from "../stores/i18n"
import { useCrawlStore } from "../stores/crawl"
import { useAuthStore } from "../stores/auth"
import { useAdminSourcesStore } from "../stores/adminSources"
import { useAdminCookiesStore } from "../stores/adminCookies"
import { useAdminCredsStore } from "../stores/adminCreds"
import { useAdminImportStore } from "../stores/adminImport"
import { useAdminUsersStore } from "../stores/adminUsers"
import { useAdminApprovalsStore } from "../stores/adminApprovals"
import { useAdminAIStore } from "../stores/adminAI"
import NavBar from "../components/NavBar.vue"
import {
  DEFAULT_TAP_ACTIONS,
  TAP_ACTIONS,
  TAP_REGION_KEYS,
  normalizeTapActions,
} from "../utils/readerTapAreas"

// The heaviest panels are split out and only fetched when their tab opens.
const SourcesPanel = defineAsyncComponent(() => import("../components/admin/SourcesPanel.vue"))
const AddPanel = defineAsyncComponent(() => import("../components/admin/AddPanel.vue"))
const UsersPanel = defineAsyncComponent(() => import("../components/admin/UsersPanel.vue"))
const ApprovalsPanel = defineAsyncComponent(() => import("../components/admin/ApprovalsPanel.vue"))
const AiPanel = defineAsyncComponent(() => import("../components/admin/AiPanel.vue"))

const i18n = useI18nStore()
const crawlStore = useCrawlStore()
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const sourcesStore = useAdminSourcesStore()
const cookiesStore = useAdminCookiesStore()
const credsStore = useAdminCredsStore()
const importStore = useAdminImportStore()
const usersStore = useAdminUsersStore()
const approvalsStore = useAdminApprovalsStore()
const aiStore = useAdminAIStore()

const tab = ref<"sources" | "cookies" | "sync" | "logs" | "tokens" | "index" | "status" | "yuedu" | "add" | "creds" | "users" | "approvals" | "proxy" | "ai" | "prefs">("yuedu")

const availableTabs = computed(() => {
  const common = ["yuedu", "sources", "sync", "logs", "tokens", "prefs"] as const
  if (!auth.isAdmin) return common
  return [
    ...common,
    "add",
    "users",
    "approvals",
    "index",
    "status",
    "proxy",
    "ai",
  ] as const
})

const prefs = ref({
  font: "sans",
  font_size: 16,
  language: "zh",
  theme: "light",
  show_covers: true,
  show_content_images: true,
  tap_actions: { ...DEFAULT_TAP_ACTIONS },
})
const prefsSaving = ref(false)
const prefsError = ref("")
const prefsSaved = ref(false)

async function loadPrefs() {
  if (auth.user?.settings) {
    prefs.value = { ...prefs.value, ...auth.user.settings }
    if (auth.user.settings.tap_actions) {
      prefs.value.tap_actions = normalizeTapActions(auth.user.settings.tap_actions)
    }
  }
}

function tapActionLabel(action: string): string {
  return i18n.t("reader_tap_action_" + action)
}

function resetTapPrefs() {
  prefs.value.tap_actions = { ...DEFAULT_TAP_ACTIONS }
  prefsSaved.value = false
}

async function savePrefs() {
  prefsSaving.value = true
  prefsError.value = ""
  prefsSaved.value = false
  try {
    const res = await api.put<any>("/auth/me/settings", prefs.value)
    auth.user = res
    if (res.settings?.language) i18n.setLocale(res.settings.language)
    if (res.settings?.theme === "dark") {
      auth.setDark(true)
    } else if (res.settings?.theme === "light") {
      auth.setDark(false)
    }
    prefsSaved.value = true
  } catch (e) {
    prefsError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  } finally {
    prefsSaving.value = false
  }
}

const accountForm = ref({ current_password: "", new_password: "", email: "", nickname: "" })
const accountError = ref("")
const accountMessage = ref("")

async function changePassword() {
  accountError.value = ""
  accountMessage.value = ""
  try {
    await api.put("/auth/me/password", {
      current_password: accountForm.value.current_password,
      new_password: accountForm.value.new_password,
    })
    accountMessage.value = i18n.t('admin_password_changed')
    accountForm.value.current_password = ""
    accountForm.value.new_password = ""
  } catch (e) {
    accountError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  }
}

async function bindEmail() {
  accountError.value = ""
  accountMessage.value = ""
  try {
    const res = await api.put<any>("/auth/me/email", {
      email: accountForm.value.email,
    })
    auth.user = res
    accountMessage.value = i18n.t('admin_email_bound')
  } catch (e) {
    accountError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  }
}

async function saveNickname() {
  accountError.value = ""
  accountMessage.value = ""
  try {
    const res = await api.put<any>("/auth/me/nickname", {
      nickname: accountForm.value.nickname,
    })
    auth.user = res
    accountMessage.value = i18n.t('admin_nickname_saved')
  } catch (e) {
    accountError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  }
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

const crawlAllSourceId = ref("")
const crawlExcludeTags = ref("耽美, BL")
const crawlExcludeCategories = ref("")
const crawlTaskError = ref("")
const crawlTaskLoading = ref(false)

const crawlTaskProgress = computed(() => {
  const task = crawlStore.activeTask
  const progress = task?.progress || {}
  const max = task?.max_pages || 1
  const pages = progress.pages_checked || 0
  const pagePct = task?.max_pages > 0 ? (pages / max) * 100 : 0
  const chapterTotal = progress.current_chapters_total || 0
  const chapterDone = progress.current_chapters_created || 0
  const chapterPct = chapterTotal ? (chapterDone / chapterTotal) * 100 : 0
  return Math.min(100, Math.round(Math.max(pagePct, chapterPct)))
})

const yueduUrl = ref("")
const yueduJsonText = ref("")
const yueduImporting = ref(false)
const yueduSyncImporting = ref(false)
const yueduCookie = ref("")
const yueduDiscover = ref(true)
const yueduIsR18 = ref(false)
const yueduScope = ref("personal")
const yueduShowContributor = ref(true)
const yueduExcludeTags = ref("耽美, BL")
const yueduExcludeCategories = ref("")
const yueduSyncResult = ref<any>(null)
const yueduSyncError = ref("")

function splitFilterText(value: string): string[] {
  return [...new Set(value.split(/[,，、;；\n]+/).map((item) => item.trim()).filter(Boolean))]
}

// Proxy config
const proxyEnabled = ref(false)
const proxyHttps = ref("")
const proxyHttp = ref("")
const proxySaving = ref(false)

async function loadProxyConfig() {
  try {
    const res = await api.get("/admin/proxy") as any
    proxyEnabled.value = res.enabled
    proxyHttps.value = res.https_proxy || ""
    proxyHttp.value = res.http_proxy || ""
  } catch {}
}

async function saveProxyConfig() {
  proxySaving.value = true
  try {
    const res = await api.put("/admin/proxy", {
      enabled: proxyEnabled.value,
      https_proxy: proxyHttps.value,
      http_proxy: proxyHttp.value,
    }) as any
    proxyEnabled.value = res.enabled
    proxyHttps.value = res.https_proxy || ""
    proxyHttp.value = res.http_proxy || ""
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('admin_proxy_save_failed'))
  } finally {
    proxySaving.value = false
  }
}

const yueduPreviewing = ref(false)
const yueduResult = ref<any>(null)
const yueduError = ref("")
const yueduPreview = ref<any>(null)

async function yueduImport() {
  yueduError.value = ""
  yueduResult.value = null
  yueduImporting.value = true
  try {
    const body: any = {}
    if (yueduUrl.value) body.url = yueduUrl.value
    if (yueduJsonText.value) body.json_text = yueduJsonText.value
    body.is_r18 = yueduIsR18.value
    body.scope = yueduScope.value
    body.show_contributor = yueduShowContributor.value
    if (yueduCookie.value.trim()) body.cookie = yueduCookie.value.trim()
    yueduResult.value = await api.post("/yuedu/import", body)
    await sourcesStore.loadSources()
    await credsStore.loadCreds()
    await cookiesStore.loadCookies()
    if (
      yueduResult.value.status !== "pending_approval" &&
      yueduResult.value.sources?.length
    ) {
      tab.value = "sources"
      sourcesStore.expandedSourceId = yueduResult.value.sources[0].id
      window.scrollTo({ top: 0, behavior: "smooth" })
    }
  } catch (e) {
    yueduError.value = e instanceof Error ? e.message : i18n.t('admin_import_failed')
  } finally {
    yueduImporting.value = false
  }
}

async function yueduImportAndSync() {
  yueduSyncError.value = ""
  yueduSyncResult.value = null
  yueduSyncImporting.value = true
  try {
    const body: any = { discover: yueduDiscover.value, is_r18: yueduIsR18.value }
    body.scope = yueduScope.value
    body.show_contributor = yueduShowContributor.value
    body.exclude_tags = splitFilterText(yueduExcludeTags.value)
    body.exclude_categories = splitFilterText(yueduExcludeCategories.value)
    if (yueduUrl.value) body.url = yueduUrl.value
    if (yueduJsonText.value) body.json_text = yueduJsonText.value
    if (yueduCookie.value.trim()) body.cookie = yueduCookie.value.trim()
    yueduSyncResult.value = await api.post("/yuedu/import-task", body)
    await sourcesStore.loadSources()
    await cookiesStore.loadCookies()
    if (yueduSyncResult.value?.tasks?.length) {
      await crawlStore.setTask(yueduSyncResult.value.tasks[0])
      router.push("/sync")
    }
  } catch (e) {
    yueduSyncError.value = e instanceof Error ? e.message : "Import & Sync failed"
  } finally {
    yueduSyncImporting.value = false
  }
}

async function yueduPreviewAction() {
  yueduError.value = ""
  yueduPreview.value = null
  yueduPreviewing.value = true
  try {
    const body: any = {}
    if (yueduUrl.value) body.url = yueduUrl.value
    if (yueduJsonText.value) body.json_text = yueduJsonText.value
    body.scope = yueduScope.value
    body.show_contributor = yueduShowContributor.value
    yueduPreview.value = await api.post("/yuedu/preview", body)
  } catch (e) {
    yueduError.value = e instanceof Error ? e.message : i18n.t('admin_preview_failed')
  } finally {
    yueduPreviewing.value = false
  }
}

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
    syncError.value = e instanceof Error ? e.message : i18n.t('admin_sync_failed')
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
    bookshelfError.value = e instanceof Error ? e.message : i18n.t('admin_sync_failed')
  } finally {
    bookshelfLoading.value = false
  }
}

async function startCrawlAll() {
  crawlTaskError.value = ""
  if (!crawlAllSourceId.value) {
    crawlTaskError.value = i18n.t('admin_crawl_source_required')
    return
  }
  crawlTaskLoading.value = true
  try {
    const task = await api.post<any>("/crawl/tasks", {
      source: crawlAllSourceId.value,
      max_pages: 0,
      exclude_tags: splitFilterText(crawlExcludeTags.value),
      exclude_categories: splitFilterText(crawlExcludeCategories.value),
    })
    await crawlStore.setTask(task)
  } catch (e) {
    crawlTaskError.value = e instanceof Error ? e.message : i18n.t('admin_crawl_start_failed')
  } finally {
    crawlTaskLoading.value = false
  }
}

async function pauseCrawlTask() {
  try {
    await crawlStore.pauseTask()
  } catch (e) {
    crawlTaskError.value = e instanceof Error ? e.message : i18n.t('admin_crawl_pause_failed')
  }
}

async function resumeCrawlTask() {
  try {
    await crawlStore.resumeTask()
  } catch (e) {
    crawlTaskError.value = e instanceof Error ? e.message : i18n.t('admin_crawl_resume_failed')
  }
}

async function cancelCrawlTask() {
  if (!confirm(i18n.t('admin_crawl_cancel_confirm'))) return
  try {
    await crawlStore.cancelTask()
  } catch (e) {
    crawlTaskError.value = e instanceof Error ? e.message : i18n.t('admin_crawl_cancel_failed')
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
    tokenError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  }
}

async function revokeToken(id: string) {
  await api.delete("/tokens/" + id)
  await loadTokens()
}

const indexStats = ref<any>(null)
const indexRebuilding = ref(false)
const indexError = ref("")
//: Progress of the current/last search-index rebuild (see
//: ``GET /search/index/rebuild/status``).  The rebuild runs in the backend in
//: the background -- it reads every chapter file, so it is hours of work that
//: used to sit inside the POST request with no feedback at all.
const indexProgress = ref<any>(null)
const indexCancelling = ref(false)
let indexPollTimer: number | null = null

async function loadIndexStats() {
  indexError.value = ""
  try {
    indexStats.value = await api.get<any>("/search/index/stats")
  } catch (e) {
    indexError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
    indexStats.value = null
  }
}

function stopIndexPolling() {
  if (indexPollTimer !== null) {
    window.clearInterval(indexPollTimer)
    indexPollTimer = null
  }
}

function ensureIndexPolling() {
  if (indexPollTimer !== null) return
  indexPollTimer = window.setInterval(() => void loadIndexProgress(), 2000)
}

async function loadIndexProgress(initial = false) {
  try {
    const progress = await api.get<any>("/search/index/rebuild/status")
    indexProgress.value = progress
    indexRebuilding.value = Boolean(progress?.running)
    if (progress?.running) {
      ensureIndexPolling()
    } else {
      stopIndexPolling()
      // The stats card is what people look at after a rebuild: refresh it once
      // the run finishes (but not on the first page load, loadIndexStats runs
      // there anyway).
      if (!initial) await loadIndexStats()
    }
  } catch {
    /* progress display must never break the settings page */
  }
}

async function rebuildIndex() {
  indexError.value = ""
  try {
    const res = await api.post<any>("/search/index/rebuild")
    indexProgress.value = res?.progress ?? null
    indexRebuilding.value = Boolean(indexProgress.value?.running)
    if (indexRebuilding.value) ensureIndexPolling()
    else await loadIndexStats()
  } catch (e) {
    indexError.value = e instanceof Error ? e.message : i18n.t('admin_rebuild_failed')
  }
}

async function cancelIndexRebuild() {
  indexCancelling.value = true
  try {
    indexProgress.value = await api.post<any>("/search/index/rebuild/cancel")
  } catch (e) {
    indexError.value = e instanceof Error ? e.message : i18n.t('admin_rebuild_failed')
  } finally {
    indexCancelling.value = false
  }
}

const indexPercent = computed(() => {
  const p = indexProgress.value
  if (!p) return 0
  if (p.phase === "done") return 100
  return Math.max(0, Math.min(100, Number(p.percent) || 0))
})

const indexElapsedSeconds = computed(() => {
  const started = indexProgress.value?.started_at
  if (!started) return 0
  const start = new Date(started).getTime()
  if (!Number.isFinite(start)) return 0
  return Math.max(0, Math.round((Date.now() - start) / 1000))
})

function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return "0s"
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.round(seconds % 60)
  if (h) return `${h}h ${m}m`
  if (m) return `${m}m ${s}s`
  return `${s}s`
}

const indexElapsed = computed(() => formatDuration(indexElapsedSeconds.value))

const indexEta = computed(() => {
  const percent = indexPercent.value
  if (!indexProgress.value?.running || percent < 2) return ""
  const remaining = (indexElapsedSeconds.value * (100 - percent)) / percent
  return formatDuration(Math.round(remaining))
})

const indexPhaseLabel = computed(() => {
  const phase = indexProgress.value?.phase
  if (phase === "books") return i18n.t('admin_index_phase_books')
  if (phase === "chapters") return i18n.t('admin_index_phase_chapters')
  if (phase === "done") return i18n.t('admin_index_phase_done')
  if (phase === "failed") return i18n.t('admin_index_phase_failed')
  if (phase === "cancelled") return i18n.t('admin_index_phase_cancelled')
  if (phase === "stale") return i18n.t('admin_index_phase_stale')
  return i18n.t('admin_index_rebuilding')
})

// Novel/comic classification maintenance: the migration backfills existing
// books, this re-runs the rules (optionally reading chapter bodies) so a new
// image source does not need a manual DB fix.
const kindScanContent = ref(true)
const kindForce = ref(false)
const kindReclassifying = ref(false)
const kindResult = ref("")
const kindError = ref("")

async function reclassifyKinds() {
  kindReclassifying.value = true
  kindError.value = ""
  kindResult.value = ""
  try {
    const res = await api.post<any>(
      "/books/reclassify?scan_content=" + (kindScanContent.value ? "true" : "false")
        + "&force=" + (kindForce.value ? "true" : "false"),
    )
    kindResult.value = i18n.t("admin_kind_done", {
      books: res.books ?? 0,
      comics: res.comics ?? 0,
      novels: res.novels ?? 0,
      updated: res.updated ?? 0,
    })
    // The endpoint also refreshes the search index for the books that changed;
    // surface a failure there instead of reporting a clean success.
    if (res.index?.error) {
      kindError.value = i18n.t("admin_kind_index_failed", { error: res.index.error })
    }
  } catch (e) {
    kindError.value = e instanceof Error ? e.message : i18n.t('admin_kind_failed')
  } finally {
    kindReclassifying.value = false
  }
}

const healthStatus = ref<any>(null)

async function loadStatus() {
  try {
    healthStatus.value = await api.get<any>("/health/detailed")
  } catch { healthStatus.value = null }
}

async function loadLogs() {
  logs.value = await api.get<any[]>("/crawl/tasks?limit=100")
  const active = logs.value.find((t) => ["pending", "running", "paused"].includes(t.status))
  if (active && (!crawlStore.activeTask || crawlStore.activeTask.id !== active.id)) {
    await crawlStore.setTask(active)
  }
}

onMounted(async () => {
  // Deep link: the reader's "open AI settings" button navigates to
  // /admin?tab=ai so the user lands on the right panel.
  const requested = route.query.tab
  if (typeof requested === "string" && (availableTabs.value as readonly string[]).includes(requested)) {
    tab.value = requested as typeof tab.value
  }
  await sourcesStore.loadSources()
  await loadPrefs()
  accountForm.value.nickname = auth.user?.nickname || ""
  await credsStore.loadCreds()
  await cookiesStore.loadCookies()
  if (auth.isAdmin) {
    await loadStatus()
    await loadIndexStats()
    await loadIndexProgress(true)
    await usersStore.loadUsers()
    await usersStore.loadRegistrationApproval()
    await approvalsStore.loadApprovals()
    await importStore.loadLocalRoots()
    loadProxyConfig()
    await aiStore.loadAIConfig()
    await aiStore.loadIndexedBooks()
  }
  await loadTokens()
  await loadLogs()
  if (crawlStore.activeTask?.id && !["completed", "failed", "cancelled", "completed_with_errors"].includes(crawlStore.activeTask.status)) {
    crawlStore.startPolling(crawlStore.activeTask.id)
  }
})

onUnmounted(() => {
  // Polling lives in the global crawl store so the task survives route changes.
  stopIndexPolling()
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-4xl mx-auto px-4 py-8">
      <h1 class="text-2xl font-bold mb-6">{{ i18n.t('admin_title') }}</h1>

      <div class="flex gap-1 mb-8 border-b border-border flex-wrap">
        <button
          v-for="t in availableTabs"
          :key="t"
          @click="tab = t"
          class="px-4 py-2 text-sm transition-colors -mb-px"
          :class="tab === t
            ? 'border-b-2 border-accent text-accent font-medium'
            : 'text-muted dark:text-gray-400 hover:text-ink'"
        >{{ i18n.t('admin_tab_' + t) }}</button>
      </div>

      <SourcesPanel v-if="tab === 'sources'" />

      <section v-if="tab === 'cookies'" class="space-y-6">
        <div class="mb-4 p-4 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950 text-sm">
          <p class="font-medium mb-1">{{ i18n.t('admin_cookie_help_title') }}</p>
          <ol class="list-decimal list-inside space-y-1 text-muted dark:text-gray-300">
            <li>{{ i18n.t('admin_cookie_help_step1') }}</li>
            <li>{{ i18n.t('admin_cookie_help_step2') }}</li>
            <li>{{ i18n.t('admin_cookie_help_step3') }}</li>
            <li>{{ i18n.t('admin_cookie_help_step4') }}</li>
          </ol>
          <p class="mt-2">
            <a href="/docs/cookie-guide.md" target="_blank" class="text-accent hover:underline">{{ i18n.t('admin_cookie_help_link') }}</a>
          </p>
        </div>
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_add_cookie') }}</h2>
          <div class="space-y-3 mb-3">
            <input v-model="cookiesStore.cookieForm.source" :placeholder="i18n.t('admin_placeholder_source')" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="cookiesStore.cookieForm.expired_at" type="datetime-local" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <textarea v-model="cookiesStore.cookieForm.cookie_data" :placeholder="i18n.t('admin_placeholder_cookie')" rows="3" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y" />
          </div>
          <p v-if="cookiesStore.cookieError" class="text-sm text-red-600 mb-2">{{ cookiesStore.cookieError }}</p>
          <div class="flex gap-3">
            <button @click="cookiesStore.createCookie" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">{{ i18n.t('admin_save_cookie') }}</button>
            <button @click="cookiesStore.testCookie" :disabled="cookiesStore.cookieTesting" class="px-4 py-2 rounded border border-accent text-accent text-sm font-medium hover:bg-accent/10 disabled:opacity-50">
              {{ cookiesStore.cookieTesting ? i18n.t('admin_testing') : i18n.t('admin_test_cookie') }}
            </button>
            <button v-if="cookiesStore.cookieEditingId" @click="cookiesStore.cookieEditingId = ''; cookiesStore.cookieForm = { source: '', cookie_data: '', expired_at: '' }" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-accent/5">{{ i18n.t('admin_cancel') }}</button>
          </div>
          <div v-if="cookiesStore.cookieTestResult" class="mt-3 p-3 rounded bg-green-50 dark:bg-green-950 text-sm">
            <p class="font-medium text-green-700 dark:text-green-400">{{ cookiesStore.cookieTestResult.message }}</p>
            <div v-if="cookiesStore.cookieTestResult.sample_books" class="mt-2 space-y-1 text-xs text-muted dark:text-gray-400">
              <p v-for="b in cookiesStore.cookieTestResult.sample_books" :key="b.title">{{ b.title }} &mdash; {{ b.author }}</p>
            </div>
          </div>
          <p v-if="cookiesStore.cookieTestError" class="text-sm text-red-600 mt-2">{{ cookiesStore.cookieTestError }}</p>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="c in cookiesStore.cookies" :key="c.id" class="px-4 py-3 flex items-center justify-between">
            <div class="flex items-center gap-3">
              <span class="text-sm font-medium">{{ c.source }}</span>
              <span v-if="c.expired_at" class="text-xs px-1.5 py-0.5 rounded-full" :class="new Date(c.expired_at) < new Date() ? 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300' : new Date(c.expired_at) < new Date(Date.now() + 3*86400000) ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300' : 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'">
                {{ new Date(c.expired_at) < new Date() ? i18n.t('admin_cookie_expired') : new Date(c.expired_at) < new Date(Date.now() + 3*86400000) ? i18n.t('admin_cookie_expiring') : i18n.t('admin_cookie_valid') }}
              </span>
            </div>
            <div class="flex items-center gap-3">
              <button @click="cookiesStore.editCookie(c)" class="text-xs text-accent hover:underline">{{ i18n.t('admin_edit') }}</button>
              <button @click="cookiesStore.deleteCookie(c.id)" class="text-xs text-red-500 hover:text-red-700">{{ i18n.t('admin_delete') }}</button>
            </div>
          </div>
          <p v-if="cookiesStore.cookies.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_cookies') }}</p>
        </div>
      </section>

      <section v-if="tab === 'sync'" class="space-y-6">
        <div v-if="auth.isAdmin" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
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
            <p>{{ i18n.t('admin_created_chapters') }}: {{ syncResult.created_chapters }} / {{ i18n.t('admin_skipped') }}: {{ syncResult.skipped_chapters }}<span v-if="syncResult.failed_chapters?.length"> / {{ i18n.t('admin_failed') }}: {{ syncResult.failed_chapters.length }}</span></p>
          </div>
        </div>

        <div v-if="auth.isAdmin" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mt-4">
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
            <p>{{ i18n.t('admin_tab_sources') }}: {{ bookshelfResult.source_id }}</p>
            <p>{{ i18n.t('admin_total') }}: {{ bookshelfResult.total }}</p>
            <div v-for="(r, i) in bookshelfResult.results" :key="i" class="mt-2 text-xs">
              <span :class="r.status === 'ok' ? 'text-green-700' : 'text-red-600'">
                {{ r.status === 'ok' ? 'OK' : i18n.t('admin_failed') }}: {{ r.book_id || r.url }}
              </span>
              <span v-if="r.error" class="text-red-500 ml-1">{{ r.error }}</span>
            </div>
          </div>
        </div>

        <div v-if="auth.isAdmin" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mt-4">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_full_site_sync') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_full_site_hint') }}</p>
          <div class="flex gap-3 mb-3">
            <input v-model="crawlAllSourceId" :placeholder="i18n.t('admin_placeholder_source')" class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <label class="block">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_exclude_tags') }}</span>
              <input v-model="crawlExcludeTags" :placeholder="i18n.t('admin_exclude_tags_placeholder')" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            </label>
            <label class="block">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_exclude_categories') }}</span>
              <input v-model="crawlExcludeCategories" :placeholder="i18n.t('admin_exclude_categories_placeholder')" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            </label>
          </div>
          <p v-if="crawlTaskError" class="text-sm text-red-600 mb-2">{{ crawlTaskError }}</p>
          <button @click="startCrawlAll" :disabled="crawlTaskLoading" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
            {{ crawlTaskLoading ? i18n.t('admin_starting_short') : i18n.t('admin_start_full_site') }}
          </button>
          <div v-if="crawlStore.activeTask" class="mt-4 p-3 rounded bg-green-50 text-sm">
            <p>{{ i18n.t('admin_task') }}: {{ crawlStore.activeTask.id }}</p>
            <p>{{ i18n.t('admin_status') }}: {{ crawlStore.activeTask.status }}</p>
            <div class="flex gap-2 mt-2">
              <button
                v-if="crawlStore.activeTask.status === 'running'"
                @click="pauseCrawlTask"
                class="px-3 py-1 text-xs border border-border rounded hover:bg-white/60"
              >{{ i18n.t('sync_pause') }}</button>
              <button
                v-if="crawlStore.activeTask.status === 'paused'"
                @click="resumeCrawlTask"
                class="px-3 py-1 text-xs border border-green-600 text-green-700 rounded hover:bg-green-50"
              >{{ i18n.t('admin_resume') }}</button>
              <button
                v-if="!['completed', 'failed', 'cancelled', 'completed_with_errors'].includes(crawlStore.activeTask.status)"
                @click="cancelCrawlTask"
                class="px-3 py-1 text-xs border border-red-500 text-red-600 rounded hover:bg-red-50"
              >{{ i18n.t('sync_cancel') }}</button>
            </div>
            <div v-if="!['completed', 'failed', 'cancelled', 'completed_with_errors'].includes(crawlStore.activeTask.status)" class="mt-3">
              <div class="h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden">
                <div class="h-full bg-accent transition-all" :style="{ width: crawlTaskProgress + '%' }"></div>
              </div>
              <p class="text-xs text-muted dark:text-gray-400 mt-1">
                {{ i18n.t('admin_checked_found', {
                  pages: crawlStore.activeTask.progress?.pages_checked || 0,
                  found: crawlStore.activeTask.progress?.books_found || 0,
                }) }}
                <span v-if="crawlStore.activeTask.progress?.current_book">
                  {{ i18n.t('admin_syncing_book', {
                    title: crawlStore.activeTask.progress.current_book,
                    done: crawlStore.activeTask.progress.current_chapters_created || 0,
                    total: crawlStore.activeTask.progress.current_chapters_total || 0,
                  }) }}
                </span>
              </p>
            </div>
            <p v-if="crawlStore.activeTask.result">{{ i18n.t('admin_full_result', {
              found: crawlStore.activeTask.result.books_found,
              synced: crawlStore.activeTask.result.books_synced,
              failed: crawlStore.activeTask.result.books_failed,
              chapters: crawlStore.activeTask.result.chapters_created,
            }) }}</p>
            <p v-if="crawlStore.activeTask.error" class="text-red-600 mt-1">{{ crawlStore.activeTask.error }}</p>
          </div>
        </div>

        <div v-else class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <p class="text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_sync_go_page') }}</p>
          <router-link to="/sync" class="inline-block mt-2 text-sm text-accent hover:underline">{{ i18n.t('nav_sync') }}</router-link>
        </div>
      </section>

      <section v-if="tab === 'logs'" class="space-y-6">
        <button @click="loadLogs" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-surface dark:bg-gray-900 transition-colors mb-4">{{ i18n.t('admin_refresh') }}</button>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="log in logs" :key="log.id" class="px-4 py-3">
            <div class="flex items-center gap-2 mb-1">
              <span class="text-sm font-medium">{{ log.source }}</span>
              <span class="text-xs px-1.5 py-0.5 rounded-full" :class="log.status === 'completed' ? 'bg-green-100 text-green-700' : log.status === 'completed_with_errors' || log.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-yellow-100 text-yellow-700'">{{ log.status }}</span>
            </div>
            <p class="text-xs text-muted dark:text-gray-400">
              {{ i18n.t('admin_started') }}: {{ log.started_at ? new Date(log.started_at).toLocaleString() : '-' }}
              &middot; {{ i18n.t('admin_finished') }}: {{ log.finished_at ? new Date(log.finished_at).toLocaleString() : '-' }}
            </p>
            <p v-if="log.error" class="text-xs text-red-600 mt-1">{{ log.error }}</p>
            <div v-if="log.result?.details?.length" class="mt-3 space-y-1.5">
              <div v-for="(item, index) in log.result.details" :key="log.id + '-detail-' + index" class="rounded border border-border px-2.5 py-2 text-xs dark:border-gray-700">
                <div class="flex items-center justify-between gap-2">
                  <span class="font-medium truncate">{{ item.title || item.name || item.url }}</span>
                  <span :class="item.synced ? 'text-green-600' : item.filtered ? 'text-gray-500' : 'text-red-600'">{{ item.synced ? i18n.t('sync_synced') : item.filtered ? i18n.t('sync_filtered') : i18n.t('sync_failed') }}</span>
                </div>
                <p v-if="item.error || item.filter_reason" class="mt-1 text-red-600">{{ item.error || item.filter_reason }}</p>
                <p v-if="item.chapters_failed" class="mt-1 text-red-600">{{ i18n.t('sync_chapters_failed') }}: {{ item.chapters_failed }}</p>
              </div>
            </div>
          </div>
          <p v-if="logs.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_logs') }}</p>
        </div>
      </section>

      <section v-if="tab === 'index'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-2">{{ i18n.t('admin_index_title') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-4">{{ i18n.t('admin_index_hint') }}</p>
          <p v-if="indexError" class="text-sm text-red-600 mb-3">{{ indexError }}</p>
          <div v-if="indexStats" class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
            <div class="p-4 rounded-lg border border-border dark:border-gray-700">
              <p class="text-xs font-medium text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_index_books') }}</p>
              <p class="text-lg font-semibold">{{ indexStats.books?.documents ?? '0' }}</p>
              <p class="text-xs text-muted dark:text-gray-400 mt-1">
                {{ indexStats.books?.is_indexing
                  ? i18n.t('admin_index_indexing')
                  : i18n.t('admin_index_last_update') + ': ' + (indexStats.books?.last_update ? new Date(indexStats.books.last_update).toLocaleString() : i18n.t('admin_index_unknown')) }}
              </p>
            </div>
            <div class="p-4 rounded-lg border border-border dark:border-gray-700">
              <p class="text-xs font-medium text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_index_chapters') }}</p>
              <p class="text-lg font-semibold">{{ indexStats.chapters?.documents ?? '0' }}</p>
              <p class="text-xs text-muted dark:text-gray-400 mt-1">
                {{ indexStats.chapters?.is_indexing
                  ? i18n.t('admin_index_indexing')
                  : i18n.t('admin_index_last_update') + ': ' + (indexStats.chapters?.last_update ? new Date(indexStats.chapters.last_update).toLocaleString() : i18n.t('admin_index_unknown')) }}
              </p>
            </div>
          </div>
          <div v-if="indexProgress && indexProgress.phase && indexProgress.phase !== 'idle'" class="mb-4 p-3 rounded-lg border border-border dark:border-gray-700">
            <div class="flex items-center justify-between text-xs mb-1">
              <span class="font-medium">{{ indexPhaseLabel }}</span>
              <span class="text-muted dark:text-gray-400">{{ indexPercent.toFixed(1) }}%</span>
            </div>
            <div class="h-2 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
              <div
                class="h-full bg-accent transition-all duration-500"
                :style="{ width: indexPercent + '%' }"
              ></div>
            </div>
            <div class="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-muted dark:text-gray-400">
              <span>{{ i18n.t('admin_index_progress_books', { done: indexProgress.books_done ?? 0, total: indexProgress.books_total ?? 0 }) }}</span>
              <span>{{ i18n.t('admin_index_progress_chapters', { done: indexProgress.chapters_done ?? 0, total: indexProgress.chapters_total ?? 0 }) }}</span>
              <span>{{ i18n.t('admin_index_progress_documents', { done: indexProgress.documents_done ?? 0, total: indexProgress.documents_total ?? 0 }) }}</span>
              <span v-if="indexProgress.running">
                {{ i18n.t('admin_index_progress_elapsed', { elapsed: indexElapsed }) }}<template v-if="indexEta"> · {{ i18n.t('admin_index_progress_eta', { eta: indexEta }) }}</template>
              </span>
            </div>
            <p v-if="indexProgress.message" class="mt-2 text-xs">{{ indexProgress.message }}</p>
            <p v-if="indexProgress.error" class="mt-1 text-xs text-red-600 break-words">{{ indexProgress.error }}</p>
            <p class="mt-1 text-[11px] text-muted dark:text-gray-400">{{ i18n.t('admin_index_progress_note') }}</p>
            <button
              v-if="indexProgress.running"
              @click="cancelIndexRebuild"
              :disabled="indexCancelling || indexProgress.cancel_requested"
              class="mt-2 px-3 py-1 text-xs border border-border dark:border-gray-700 rounded hover:bg-accent/5 disabled:opacity-50"
            >{{ indexProgress.cancel_requested ? i18n.t('admin_index_cancelling') : i18n.t('admin_index_cancel') }}</button>
          </div>
          <button
            @click="rebuildIndex"
            :disabled="indexRebuilding"
            class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >{{ indexRebuilding ? i18n.t('admin_index_rebuilding') : i18n.t('admin_index_rebuild') }}</button>
        </div>

        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-2">{{ i18n.t('admin_kind_title') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-4">{{ i18n.t('admin_kind_hint') }}</p>
          <p v-if="kindError" class="text-sm text-red-600 mb-3">{{ kindError }}</p>
          <p v-if="kindResult" class="text-sm text-green-700 mb-3">{{ kindResult }}</p>
          <label class="flex items-center gap-2 mb-4 text-xs text-muted dark:text-gray-400 cursor-pointer">
            <input v-model="kindScanContent" type="checkbox" class="rounded" />
            <span>{{ i18n.t('admin_kind_scan') }}</span>
          </label>
          <label class="flex items-center gap-2 mb-4 text-xs text-muted dark:text-gray-400 cursor-pointer">
            <input v-model="kindForce" type="checkbox" class="rounded" />
            <span>{{ i18n.t('admin_kind_force') }}</span>
          </label>
          <button
            @click="reclassifyKinds"
            :disabled="kindReclassifying"
            class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >{{ kindReclassifying ? i18n.t('admin_kind_running') : i18n.t('admin_kind_run') }}</button>
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

      <section v-if="tab === 'prefs'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_tab_prefs') }}</h2>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
            <label class="block">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_pref_font') }}</span>
              <select v-model="prefs.font" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
                <option value="sans">Sans</option>
                <option value="serif">Serif</option>
                <option value="mono">Mono</option>
              </select>
            </label>
            <label class="block">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_pref_font_size') }}</span>
              <input v-model.number="prefs.font_size" type="number" min="12" max="32" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            </label>
            <label class="block">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_pref_language') }}</span>
              <select v-model="prefs.language" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
                <option value="zh">中文</option>
                <option value="en">English</option>
              </select>
            </label>
            <label class="block">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_pref_theme') }}</span>
              <select v-model="prefs.theme" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
                <option value="light">{{ i18n.t('reader_light_mode') }}</option>
                <option value="dark">{{ i18n.t('reader_dark_mode') }}</option>
              </select>
            </label>
            <label class="flex items-center justify-between gap-3 text-sm cursor-pointer">
              <span>{{ i18n.t('admin_pref_show_covers') }}</span>
              <input type="checkbox" v-model="prefs.show_covers" class="rounded" />
            </label>
            <label class="flex items-center justify-between gap-3 text-sm cursor-pointer">
              <span>{{ i18n.t('admin_pref_show_content_images') }}</span>
              <input type="checkbox" v-model="prefs.show_content_images" class="rounded" />
            </label>
          </div>
          <div class="border-t border-border dark:border-gray-700 pt-4 mt-4">
            <div class="flex items-center justify-between mb-2">
              <h3 class="text-sm font-semibold">{{ i18n.t('admin_pref_tap_areas') }}</h3>
              <button @click="resetTapPrefs" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700">{{ i18n.t('admin_pref_tap_reset') }}</button>
            </div>
            <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_pref_tap_areas_hint') }}</p>
            <div class="grid grid-cols-3 gap-2 max-w-sm">
              <label v-for="region in TAP_REGION_KEYS" :key="region" class="block">
                <span class="block text-center text-xs text-muted dark:text-gray-400 mb-1">{{ region.toUpperCase() }}</span>
                <select v-model="prefs.tap_actions[region]" class="w-full px-2 py-1.5 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800">
                  <option v-for="action in TAP_ACTIONS" :key="action" :value="action">{{ tapActionLabel(action) }}</option>
                </select>
              </label>
            </div>
          </div>
          <p v-if="prefsError" class="text-sm text-red-600 mb-2">{{ prefsError }}</p>
          <p v-else-if="prefsSaved" class="text-sm text-green-600 mb-2">{{ i18n.t('admin_pref_saved') }}</p>
          <button @click="savePrefs" :disabled="prefsSaving" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">{{ prefsSaving ? i18n.t('admin_saving') : i18n.t('admin_pref_save') }}</button>
        </div>

        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_tab_account') }}</h2>
          <div class="mb-4">
            <label class="block mb-2">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_nickname') }}</span>
              <input v-model="accountForm.nickname" maxlength="48" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            </label>
            <button @click="saveNickname" class="px-4 py-2 rounded border border-accent text-accent text-sm font-medium hover:bg-accent/10">{{ i18n.t('admin_save_nickname') }}</button>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
            <label class="block">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_current_password') }}</span>
              <input v-model="accountForm.current_password" type="password" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            </label>
            <label class="block">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_new_password') }}</span>
              <input v-model="accountForm.new_password" type="password" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            </label>
          </div>
          <button @click="changePassword" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">{{ i18n.t('admin_my_change_password') }}</button>

          <div class="mt-4 pt-4 border-t border-border dark:border-gray-700">
            <label class="block mb-2">
              <span class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_bind_email') }}</span>
              <input v-model="accountForm.email" type="email" :placeholder="auth.user?.email || ''" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            </label>
            <button @click="bindEmail" class="px-4 py-2 rounded border border-accent text-accent text-sm font-medium hover:bg-accent/10">{{ i18n.t('admin_save_email') }}</button>
          </div>
          <p v-if="accountError" class="text-sm text-red-600 mt-3">{{ accountError }}</p>
          <p v-else-if="accountMessage" class="text-sm text-green-600 mt-3">{{ accountMessage }}</p>
        </div>
      </section>

      <section v-if="tab === 'yuedu'" class="space-y-6">
        <div class="p-5 rounded-lg border-2 border-accent/30 dark:border-accent/50 bg-surface dark:bg-gray-900">
          <h2 class="text-base font-bold mb-1">{{ i18n.t('admin_yuedu_title') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-5">{{ i18n.t('admin_yuedu_hint') }}</p>

          <div class="space-y-4">
            <div>
              <label class="block text-xs font-medium mb-1.5">{{ i18n.t('admin_yuedu_source_url_label') }}</label>
              <input v-model="yueduUrl" :placeholder="i18n.t('admin_yuedu_url_placeholder')" class="w-full px-3 py-2.5 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 focus:ring-2 focus:ring-accent/30 focus:border-accent" />
            </div>
            <div>
              <label class="block text-xs font-medium mb-1.5">{{ i18n.t('admin_yuedu_advanced_json_placeholder') }}</label>
              <textarea v-model="yueduJsonText" :placeholder="i18n.t('admin_yuedu_advanced_json_placeholder')" rows="4" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y font-mono text-xs" />
            </div>
            <div class="flex items-center gap-2">
              <input type="checkbox" id="yuedu-r18" v-model="yueduIsR18" class="rounded" />
              <label for="yuedu-r18" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_r18_label') }}</label>
            </div>
            <div class="flex flex-wrap items-center gap-3">
              <label for="yuedu-scope" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_scope_label') }}</label>
              <select id="yuedu-scope" v-model="yueduScope" class="px-2 py-1.5 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800">
                <option value="personal">{{ i18n.t('admin_source_personal') }}</option>
                <option value="global">{{ i18n.t('admin_source_global') }}</option>
              </select>
              <label v-if="yueduScope === 'global'" class="inline-flex items-center gap-2 text-xs text-muted dark:text-gray-400 cursor-pointer">
                <input type="checkbox" v-model="yueduShowContributor" class="rounded" />
                {{ i18n.t('admin_yuedu_show_contributor') }}
              </label>
            </div>

            <div>
              <label for="yuedu-cookie" class="block text-xs font-medium mb-1.5">{{ i18n.t('admin_yuedu_cookie_label') }}</label>
              <textarea
                id="yuedu-cookie"
                v-model="yueduCookie"
                :placeholder="i18n.t('admin_yuedu_cookie_placeholder')"
                rows="2"
                class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y font-mono text-xs"
              ></textarea>
            </div>
            <label class="inline-flex items-center gap-2 text-xs text-muted dark:text-gray-400 cursor-pointer">
              <input type="checkbox" v-model="yueduDiscover" class="rounded" />
              {{ i18n.t('admin_yuedu_discover_label') }}
            </label>
            <div v-if="yueduDiscover" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label class="block">
                <span class="block text-xs font-medium mb-1.5">{{ i18n.t('admin_exclude_tags') }}</span>
                <input v-model="yueduExcludeTags" :placeholder="i18n.t('admin_exclude_tags_placeholder')" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
              </label>
              <label class="block">
                <span class="block text-xs font-medium mb-1.5">{{ i18n.t('admin_exclude_categories') }}</span>
                <input v-model="yueduExcludeCategories" :placeholder="i18n.t('admin_exclude_categories_placeholder')" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
              </label>
            </div>
            <button
              @click="yueduImportAndSync"
              :disabled="yueduSyncImporting"
              class="w-full py-3 rounded-lg border border-accent text-accent font-semibold hover:bg-accent/10 disabled:opacity-50 transition-all text-sm"
            >{{ yueduSyncImporting ? i18n.t('admin_yuedu_importing_sync') : i18n.t('admin_yuedu_import_sync_btn') }}</button>

            <p v-if="yueduSyncError" class="text-sm text-red-600">{{ yueduSyncError }}</p>

            <button @click="yueduImport" :disabled="yueduImporting"
              class="w-full py-3 rounded-lg bg-accent text-white font-semibold hover:opacity-90 disabled:opacity-50 transition-all text-sm">
              {{ yueduImporting ? '...' : (yueduScope === 'global' ? (auth.isAdmin ? i18n.t('admin_yuedu_import_global') : i18n.t('admin_yuedu_submit_global')) : i18n.t('admin_yuedu_import_personal')) }}
            </button>

            <p v-if="yueduError" class="text-sm text-red-600 mt-3">{{ yueduError }}</p>
            <div v-if="yueduResult" class="mt-3 p-3 rounded bg-green-50 dark:bg-green-950 text-sm">
              <p class="font-medium">
                {{ yueduResult.status === 'pending_approval'
                  ? i18n.t('admin_yuedu_pending_approval', { n: yueduResult.imported })
                  : i18n.t('admin_yuedu_imported_count', { imported: yueduResult.imported, total: yueduResult.total }) }}
              </p>
              <p class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_skipped_count', { skipped: yueduResult.skipped }) }}</p>
              <p v-if="yueduResult.updated" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_updated_count', { updated: yueduResult.updated }) }}</p>
              <button
                v-if="yueduScope === 'personal'"
                @click="router.push('/sync')"
                class="mt-3 px-3 py-1.5 rounded bg-accent text-white text-xs font-medium"
              >{{ i18n.t('admin_yuedu_go_sync') }}</button>
            </div>

            <details class="mt-3">
              <summary class="text-xs text-muted dark:text-gray-400 cursor-pointer hover:text-ink">{{ i18n.t('admin_yuedu_advanced') }}</summary>
              <div class="mt-3 space-y-3">
                <div class="flex gap-3">
                  <button @click="yueduPreviewAction" :disabled="yueduPreviewing" class="px-3 py-1.5 rounded border border-border dark:border-gray-700 text-xs hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
                    {{ yueduPreviewing ? '...' : i18n.t('admin_yuedu_btn_preview') }}
                  </button>
                </div>
                <div v-if="yueduPreview" class="p-2 rounded bg-blue-50 dark:bg-blue-950 text-xs">
                  <p class="font-medium mb-1">{{ i18n.t('admin_yuedu_preview_count', { n: yueduPreview.count }) }}</p>
                  <div class="max-h-32 overflow-y-auto">
                    <p v-for="(s, i) in yueduPreview.sources" :key="i">{{ s.name }}</p>
                  </div>
                </div>
              </div>
            </details>
          </div>
        </div>
      </section>

      <AddPanel v-if="tab === 'add'" />

      <section v-if="tab === 'creds'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_add_cred') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_cred_hint') }}</p>
          <div class="grid grid-cols-3 gap-3 mb-3">
            <input v-model="credsStore.credForm.source" :placeholder="i18n.t('admin_placeholder_source')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="credsStore.credForm.username" :placeholder="i18n.t('admin_placeholder_username')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="credsStore.credForm.password" type="password" :placeholder="i18n.t('admin_placeholder_password')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <p v-if="credsStore.credError" class="text-sm text-red-600 mb-2">{{ credsStore.credError }}</p>
          <button @click="credsStore.createCred" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">{{ i18n.t('admin_save_cred') }}</button>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="c in credsStore.creds" :key="c.id" class="px-4 py-3 flex items-center justify-between flex-wrap gap-2">
            <div>
              <span class="text-sm font-medium">{{ c.source }}</span>
              <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ c.username }}</span>
            </div>
            <div class="flex items-center gap-3">
              <button @click="credsStore.autoLogin(c.id)" :disabled="credsStore.credLoggingIn[c.id]" class="px-3 py-1.5 rounded border border-green-500 text-green-600 text-xs font-medium hover:bg-green-50 dark:hover:bg-green-950 disabled:opacity-50">
                {{ credsStore.credLoggingIn[c.id] ? i18n.t('admin_logging_in') : i18n.t('admin_auto_login') }}
              </button>
              <button @click="credsStore.startManualLogin(c.id)" :disabled="credsStore.manualLoginLoading" class="px-3 py-1.5 rounded border border-blue-500 text-blue-600 text-xs font-medium hover:bg-blue-50 dark:hover:bg-blue-950 disabled:opacity-50">
                {{ i18n.t('admin_manual_login') }}
              </button>
              <button @click="credsStore.deleteCred(c.id)" class="text-xs text-red-500 hover:text-red-700">{{ i18n.t('admin_delete') }}</button>
            </div>
            <div v-if="credsStore.credLoginResult[c.id]" class="w-full mt-1 p-2 rounded bg-green-50 dark:bg-green-950 text-xs text-green-700 dark:text-green-400">
              {{ credsStore.credLoginResult[c.id].message }}
            </div>
            <p v-if="credsStore.credLoginError[c.id]" class="w-full text-xs text-red-600 mt-1">{{ credsStore.credLoginError[c.id] }}</p>
          </div>
          <p v-if="credsStore.creds.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_creds') }}</p>
        </div>
      </section>
      <UsersPanel v-if="tab === 'users'" />

      <ApprovalsPanel v-if="tab === 'approvals'" />

      <section v-if="tab === 'proxy'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 max-w-lg">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_proxy_title') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_proxy_hint') }}</p>
          <div class="flex items-center justify-between mb-4">
            <span class="text-sm">{{ i18n.t('admin_proxy_enable') }}</span>
            <button @click="proxyEnabled = !proxyEnabled" :class="proxyEnabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'" class="relative w-11 h-6 rounded-full transition-colors duration-200">
              <span :class="proxyEnabled ? 'translate-x-5' : 'translate-x-0.5'" class="absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200"></span>
            </button>
          </div>
          <div class="mb-3">
            <label class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_https_proxy') }}</label>
            <input v-model="proxyHttps" placeholder="http://127.0.0.1:7890" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <div class="mb-4">
            <label class="block text-xs text-muted dark:text-gray-400 mb-1">{{ i18n.t('admin_http_proxy') }}</label>
            <input v-model="proxyHttp" placeholder="http://127.0.0.1:7890" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <button @click="saveProxyConfig" :disabled="proxySaving" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50">
            {{ proxySaving ? i18n.t('admin_saving') : i18n.t('admin_save') }}
          </button>
        </div>
      </section>

      <AiPanel v-if="tab === 'ai'" />
    </main>
  </div>
</template>
