<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue"
import { useRouter } from "vue-router"
import { api } from "../api/client"
import { useI18nStore } from "../stores/i18n"
import { useCrawlStore } from "../stores/crawl"
import { useAuthStore } from "../stores/auth"
import NavBar from "../components/NavBar.vue"
import {
  DEFAULT_TAP_ACTIONS,
  TAP_ACTIONS,
  TAP_REGION_KEYS,
  normalizeTapActions,
} from "../utils/readerTapAreas"

const i18n = useI18nStore()
const crawlStore = useCrawlStore()
const auth = useAuthStore()
const router = useRouter()

interface Source {
  id: string
  name: string
  url: string | null
  plugin_name: string
  enabled: boolean
  is_r18: boolean
  config?: any
  owner_id?: string | null
  submitter_id?: string | null
  submitter_username?: string | null
  show_contributor?: boolean
}

interface CookieItem {
  id: string
  source: string
  cookie_data: string
  expired_at: string | null
}

const tab = ref<"sources" | "cookies" | "sync" | "logs" | "tokens" | "index" | "status" | "yuedu" | "add" | "creds" | "users" | "approvals" | "proxy" | "prefs">("yuedu")

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
  ] as const
})

const sources = ref<Source[]>([])
const sourceForm = ref({ id: "", name: "", url: "", plugin_name: "yuedu", enabled: true, is_r18: false, scope: "personal" })
const sourceConfigText = ref("")
const sourceEditingId = ref("")
const sourceError = ref("")

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

async function loadSources() {
  sources.value = await api.get<Source[]>("/sources")
}


async function deleteSource(id: string) {
  if (!confirm(i18n.t('admin_delete_source_confirm', { id }))) return
  try {
    await api.delete("/sources/" + id)
    await loadSources()
  } catch (e) {
    sourceError.value = e instanceof Error ? e.message : i18n.t('admin_delete_source_failed')
  }
}

function parseSourceConfig(value: string): any {
  let parsed: any = JSON.parse(value)
  // Some old source records were serialized twice before being written to
  // JSONB. Unwrap those values so editing does not save a quoted JSON blob.
  for (let i = 0; i < 2 && typeof parsed === "string"; i += 1) {
    parsed = JSON.parse(parsed)
  }
  return parsed
}

async function createSource() {
  sourceError.value = ""
  if (!sourceForm.value.name.trim()) {
    sourceError.value = i18n.t('admin_source_name_required')
    return
  }
  let config: any = null
  if (sourceConfigText.value.trim()) {
    try {
      config = parseSourceConfig(sourceConfigText.value)
      if (!config || typeof config !== "object" || Array.isArray(config)) {
        throw new Error("source config must be a JSON object")
      }
    } catch {
      sourceError.value = i18n.t('admin_config_json_invalid')
      return
    }
  }
  try {
    const body: any = {
      ...sourceForm.value,
      url: sourceForm.value.url || null,
      config,
    }
    if (sourceEditingId.value) {
      const current = sources.value.find((s: Source) => s.id === sourceEditingId.value)
      const isGlobal = current && !current.owner_id
      const wantGlobal = sourceForm.value.scope === "global"
      if ((wantGlobal && !isGlobal) || (!wantGlobal && isGlobal)) {
        await api.post("/sources", {
          ...body,
          id: current?.id || sourceForm.value.id,
          scope: sourceForm.value.scope,
        })
      } else {
        await api.put("/sources/" + sourceEditingId.value, body)
      }
    } else {
      await api.post("/sources", body)
    }
    await loadSources()
    resetSourceForm()
  } catch (e) {
    sourceError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  }
}

function editSource(s: Source) {
  sourceEditingId.value = s.id
  sourceForm.value = {
    id: s.id,
    name: s.name,
    url: s.url || "",
    plugin_name: s.plugin_name || "yuedu",
    enabled: s.enabled,
    is_r18: s.is_r18,
    scope: s.owner_id ? "personal" : "global",
  }
  if (!auth.isAdmin && !s.owner_id) {
    sourceForm.value.scope = "personal"
  }
  // Older records may contain the JSONB config as a serialized string.
  // Normalize it before putting it in the editor so editing never produces
  // invalid JSON such as a quoted JSON document.
  if (typeof s.config === "string") {
    try {
      sourceConfigText.value = JSON.stringify(parseSourceConfig(s.config), null, 2)
    } catch {
      sourceConfigText.value = s.config
    }
  } else {
    sourceConfigText.value = s.config ? JSON.stringify(s.config, null, 2) : ""
  }
  window.scrollTo({ top: 0, behavior: "smooth" })
}

function resetSourceForm() {
  sourceEditingId.value = ""
  sourceForm.value = { id: "", name: "", url: "", plugin_name: "yuedu", enabled: true, is_r18: false, scope: "personal" }
  sourceConfigText.value = ""
}

function canManageSource(s: Source) {
  return auth.isAdmin || s.owner_id === auth.user?.id
}

function sourceCookies(sourceId: string) {
  return cookies.value.filter((c) => c.source === sourceId)
}

function cookieExpired(c: CookieItem) {
  return !!c.expired_at && new Date(c.expired_at) < new Date()
}

function sourceCreds(sourceId: string) {
  return creds.value.filter((c) => c.source === sourceId)
}

function toggleSourceDetails(s: Source) {
  if (expandedSourceId.value === s.id) {
    expandedSourceId.value = ""
    return
  }
  expandedSourceId.value = s.id
  cookieForm.value = { source: s.id, cookie_data: "", expired_at: "" }
  cookieEditingId.value = ""
  cookieError.value = ""
  cookieTestResult.value = null
  cookieTestError.value = ""
  credForm.value = { source: s.id, username: "", password: "" }
  credError.value = ""
}

async function saveSourceCookie(s: Source) {
  cookieForm.value.source = s.id
  const existing = sourceCookies(s.id)[0]
  if (existing) {
    cookieEditingId.value = existing.id
  }
  await createCookie()
}

async function testSourceCookie(s: Source) {
  cookieForm.value.source = s.id
  await testCookie()
}

async function saveSourceCred(s: Source) {
  credForm.value.source = s.id
  await createCred()
}

const cookies = ref<CookieItem[]>([])
const cookieForm = ref({ source: "", cookie_data: "", expired_at: "" })
const cookieEditingId = ref("")
const cookieError = ref("")
const cookieTesting = ref(false)
const cookieTestResult = ref<any>(null)
const cookieTestError = ref("")

async function testCookie() {
  cookieTestError.value = ""
  cookieTestResult.value = null
  cookieTesting.value = true
  try {
    cookieTestResult.value = await api.post("/cookies/test", {
      source: cookieForm.value.source,
      cookie_data: cookieForm.value.cookie_data,
    })
  } catch (e) {
    cookieTestError.value = e instanceof Error ? e.message : i18n.t('admin_test_failed')
  } finally {
    cookieTesting.value = false
  }
}

async function loadCookies() {
  cookies.value = await api.get<CookieItem[]>("/cookies")
}

async function createCookie() {
  cookieError.value = ""
  try {
    const body: any = { source: cookieForm.value.source, cookie_data: cookieForm.value.cookie_data }
    if (cookieForm.value.expired_at) body.expired_at = cookieForm.value.expired_at
    if (cookieEditingId.value) {
      const updateBody: any = {
        expired_at: cookieForm.value.expired_at || null,
      }
      if (cookieForm.value.cookie_data.trim()) {
        updateBody.cookie_data = cookieForm.value.cookie_data
      }
      await api.put("/cookies/" + cookieEditingId.value, updateBody)
    } else {
      await api.post("/cookies", body)
    }
    await loadCookies()
    cookieEditingId.value = ""
    cookieForm.value = { source: "", cookie_data: "", expired_at: "" }
  } catch (e) {
    cookieError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  }
}

function editCookie(c: CookieItem) {
  cookieEditingId.value = c.id
  cookieForm.value = {
    source: c.source,
    cookie_data: "",
    expired_at: c.expired_at || "",
  }
  window.scrollTo({ top: 0, behavior: "smooth" })
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
const expandedSourceId = ref("")

function splitFilterText(value: string): string[] {
  return [...new Set(value.split(/[,，、;；\n]+/).map((item) => item.trim()).filter(Boolean))]
}

const localPath = ref("")
const localImporting = ref(false)
const localResult = ref<any>(null)
const localError = ref("")
const localScan = ref<any[]>([])
const localScanRoot = ref("")
const localSelected = ref<string[]>([])
const localScanning = ref(false)
const localDirectResult = ref<any>(null)
const localRoots = ref<any[]>([])
const localClass = ref<"auto" | "all" | "r18">("auto")
const localBrowserOpen = ref(false)
const localBrowserPath = ref("")
const localBrowserName = ref("")
const localBrowserParent = ref<string | null>(null)
const localBrowserDirs = ref<any[]>([])
const localBrowsing = ref(false)

const manualTitle = ref("")
const manualAuthor = ref("")
const manualStatus = ref("ongoing")
const manualDescription = ref("")
const manualTags = ref("")
const manualChaptersText = ref("")
const manualImporting = ref(false)
const manualAnalyzing = ref(false)
const manualAnalyzeResult = ref<any>(null)
const manualResult = ref<any>(null)
const manualError = ref("")
const manualClass = ref<"auto" | "all" | "r18">("auto")

function classificationValue(value: "auto" | "all" | "r18"): boolean | null {
  if (value === "all") return false
  if (value === "r18") return true
  return null
}

const creds = ref<any[]>([])
const credForm = ref({ source: "", username: "", password: "" })
const credError = ref("")
const credLoggingIn = ref<Record<string, boolean>>({})

// Manual login state
const manualLoginActive = ref(false)
const manualLoginSessionId = ref("")
const manualLoginScreenshot = ref("")
const manualLoginSourceName = ref("")
const manualLoginLoading = ref(false)
const manualLoginInput = ref("")
const manualLoginError = ref("")

// Proxy config
const proxyEnabled = ref(false)
const proxyHttps = ref("")
const proxyHttp = ref("")
const proxySaving = ref(false)
const credLoginResult = ref<Record<string, any>>({})
const credLoginError = ref<Record<string, string>>({})

async function loadCreds() {
  try {
    creds.value = await api.get<any[]>("/credentials")
  } catch { creds.value = [] }
}

async function createCred() {
  credError.value = ""
  try {
    await api.post("/credentials", credForm.value)
    await loadCreds()
    credForm.value = { source: "", username: "", password: "" }
  } catch (e) {
    credError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  }
}

async function deleteCred(id: string) {
  await api.delete("/credentials/" + id)
  await loadCreds()
}

async function autoLogin(id: string) {
  credLoggingIn.value[id] = true
  credLoginResult.value[id] = null
  credLoginError.value[id] = ""
  try {
    credLoginResult.value[id] = await api.post("/credentials/" + id + "/auto-login")
    await loadCookies()
  } catch (e) {
    credLoginError.value[id] = e instanceof Error ? e.message : i18n.t('admin_login_failed')
  } finally {
    credLoggingIn.value[id] = false
  }
}

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

async function startManualLogin(credId: string) {
  manualLoginLoading.value = true
  manualLoginError.value = ""
  try {
    const res = await api.post("/credentials/" + credId + "/manual-login/start") as any
    manualLoginSessionId.value = res.session_id
    manualLoginScreenshot.value = res.screenshot
    manualLoginSourceName.value = res.source_name
    manualLoginActive.value = true
    if (res.error) {
      manualLoginError.value = res.error
    }
  } catch (e) {
    manualLoginError.value = e instanceof Error ? e.message : "Failed to start manual login"
  } finally {
    manualLoginLoading.value = false
  }
}

function handleManualClick(e: MouseEvent) {
  if (!manualLoginActive.value) return
  const img = e.currentTarget as HTMLImageElement
  const rect = img.getBoundingClientRect()
  const scaleX = img.naturalWidth / rect.width
  const scaleY = img.naturalHeight / rect.height
  const x = Math.round((e.clientX - rect.left) * scaleX)
  const y = Math.round((e.clientY - rect.top) * scaleY)
  manualLoginLoading.value = true
  api.post("/manual-login/" + manualLoginSessionId.value + "/click", { x, y }).then((res: any) => {
    manualLoginScreenshot.value = res.screenshot
  }).catch(e => {
    manualLoginError.value = e instanceof Error ? e.message : i18n.t('admin_click_failed')
  }).finally(() => {
    manualLoginLoading.value = false
  })
}

async function handleManualType() {
  if (!manualLoginInput.value) return
  manualLoginLoading.value = true
  try {
    const res = await api.post("/manual-login/" + manualLoginSessionId.value + "/type", { text: manualLoginInput.value }) as any
    manualLoginScreenshot.value = res.screenshot
    manualLoginInput.value = ""
  } catch (e) {
    manualLoginError.value = e instanceof Error ? e.message : i18n.t('admin_type_failed')
  } finally {
    manualLoginLoading.value = false
  }
}

async function handleManualKey(key: string) {
  manualLoginLoading.value = true
  try {
    const res = await api.post("/manual-login/" + manualLoginSessionId.value + "/key", { key }) as any
    manualLoginScreenshot.value = res.screenshot
  } catch (e) {
    manualLoginError.value = e instanceof Error ? e.message : i18n.t('admin_key_failed')
  } finally {
    manualLoginLoading.value = false
  }
}

async function finishManualLogin() {
  manualLoginLoading.value = true
  try {
    await api.post("/manual-login/" + manualLoginSessionId.value + "/finish")
    manualLoginActive.value = false
    await loadCookies()
  } catch (e) {
    manualLoginError.value = e instanceof Error ? e.message : i18n.t('admin_cookie_save_failed')
  } finally {
    manualLoginLoading.value = false
  }
}

async function cancelManualLogin() {
  try {
    await api.post("/manual-login/" + manualLoginSessionId.value + "/cancel")
  } catch {}
  manualLoginActive.value = false
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
    await loadSources()
    await loadCreds()
    await loadCookies()
    if (
      yueduResult.value.status !== "pending_approval" &&
      yueduResult.value.sources?.length
    ) {
      tab.value = "sources"
      expandedSourceId.value = yueduResult.value.sources[0].id
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
    await loadSources()
    await loadCookies()
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

async function importLocal() {
  localError.value = ""
  localResult.value = null
  if (!localPath.value.trim()) {
    localError.value = i18n.t('admin_local_path_required')
    return
  }
  localImporting.value = true
  try {
    localResult.value = await api.post<any>("/sync/local", {
      path: localPath.value.trim(),
      is_r18: classificationValue(localClass.value),
    })
    await loadSources()
  } catch (e) {
    localError.value = e instanceof Error ? e.message : i18n.t('admin_local_import_failed')
  } finally {
    localImporting.value = false
  }
}

async function loadLocalRoots() {
  try {
    localRoots.value = await api.get<any[]>("/sync/local/roots")
  } catch {}
}

async function openLocalBrowser(root?: any) {
  localBrowserOpen.value = true
  localError.value = ""
  if (root) {
    await browseLocalDirectory(root.path)
  } else if (localRoots.value.length) {
    await browseLocalDirectory(localRoots.value[0].path)
  } else {
    localBrowserPath.value = ""
    localBrowserName.value = ""
    localBrowserParent.value = null
    localBrowserDirs.value = []
  }
}

async function browseLocalDirectory(path: string) {
  localBrowsing.value = true
  localError.value = ""
  try {
    const res = await api.post<any>("/sync/local/list", { path })
    localBrowserPath.value = res.path
    localBrowserName.value = res.name
    localBrowserParent.value = res.parent
    localBrowserDirs.value = res.directories || []
  } catch (e) {
    localError.value = e instanceof Error ? e.message : i18n.t('admin_local_browse_failed')
  } finally {
    localBrowsing.value = false
  }
}

function chooseLocalDirectory(path: string) {
  if (!path) return
  localPath.value = path
  localBrowserOpen.value = false
}

async function scanLocal() {
  localError.value = ""
  localResult.value = null
  localDirectResult.value = null
  localScan.value = []
  localSelected.value = []
  if (!localPath.value.trim()) {
    localError.value = i18n.t('admin_local_path_required')
    return
  }
  localScanning.value = true
  try {
    const res = await api.post<any>("/sync/local/scan", {
      path: localPath.value.trim(),
      max_depth: 3,
      is_r18: classificationValue(localClass.value),
    })
    localScanRoot.value = res.root || ""
    localScan.value = res.books || []
  } catch (e) {
    localError.value = e instanceof Error ? e.message : i18n.t('admin_local_import_failed')
  } finally {
    localScanning.value = false
  }
}

function toggleLocalBook(path: string) {
  const idx = localSelected.value.indexOf(path)
  if (idx === -1) {
    localSelected.value.push(path)
  } else {
    localSelected.value.splice(idx, 1)
  }
}

const allLocalSelected = computed(() =>
  localScan.value.length > 0 && localSelected.value.length === localScan.value.length
)

function toggleAllLocalBooks() {
  if (allLocalSelected.value) {
    localSelected.value = []
  } else {
    localSelected.value = localScan.value.map(book => book.path)
  }
}

function invertLocalBooks() {
  const selected = new Set(localSelected.value)
  localSelected.value = localScan.value
    .map(book => book.path)
    .filter(path => !selected.has(path))
}

function clearLocalBooks() {
  localSelected.value = []
}

async function importLocalSelected() {
  localError.value = ""
  localResult.value = null
  if (localSelected.value.length === 0) {
    localError.value = i18n.t('admin_local_select_required')
    return
  }
  localImporting.value = true
  try {
    localResult.value = await api.post<any>("/sync/local/import", {
      path: localPath.value.trim(),
      book_paths: localSelected.value,
      is_r18: classificationValue(localClass.value),
    })
    await loadSources()
  } catch (e) {
    localError.value = e instanceof Error ? e.message : i18n.t('admin_local_import_failed')
  } finally {
    localImporting.value = false
  }
}

async function importLocalAll() {
  localError.value = ""
  localResult.value = null
  if (localScan.value.length === 0) {
    localError.value = i18n.t('admin_local_select_required')
    return
  }
  localImporting.value = true
  try {
    localResult.value = await api.post<any>("/sync/local/import", {
      path: localPath.value.trim(),
      book_paths: localScan.value.map(book => book.path),
      is_r18: classificationValue(localClass.value),
    })
    await loadSources()
  } catch (e) {
    localError.value = e instanceof Error ? e.message : i18n.t('admin_local_import_failed')
  } finally {
    localImporting.value = false
  }
}

async function directLocalSelected() {
  localError.value = ""
  localResult.value = null
  localDirectResult.value = null
  if (localSelected.value.length === 0) {
    localError.value = i18n.t('admin_local_select_required')
    return
  }
  localImporting.value = true
  try {
    localDirectResult.value = await api.post<any>("/sync/local/direct", {
      path: localPath.value.trim(),
      book_paths: localSelected.value,
      is_r18: classificationValue(localClass.value),
    })
  } catch (e) {
    localError.value = e instanceof Error ? e.message : i18n.t('admin_local_import_failed')
  } finally {
    localImporting.value = false
  }
}

async function onManualFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const text = await file.text()
  manualChaptersText.value = text
  await analyzeManualText(text, file.name)
}

async function analyzeManualText(sourceText?: string, filename?: string) {
  const text = (sourceText ?? manualChaptersText.value).trim()
  if (!text) {
    manualError.value = ""
    manualAnalyzeResult.value = null
    return
  }
  manualAnalyzing.value = true
  manualError.value = ""
  manualAnalyzeResult.value = null
  try {
    const res = await api.post<any>("/books/manual/analyze", {
      text,
      filename: filename || null,
      is_r18: classificationValue(manualClass.value),
    })
    manualAnalyzeResult.value = res
    if (res.title) manualTitle.value = res.title
    if (res.author && res.author !== i18n.t('admin_unknown_author')) {
      manualAuthor.value = res.author
    }
    if (res.description) manualDescription.value = res.description
    if (res.status) manualStatus.value = res.status
    if (res.tags?.length) manualTags.value = res.tags.join(", ")
  } catch (e) {
    manualError.value = e instanceof Error ? e.message : i18n.t('admin_manual_analyze_failed')
  } finally {
    manualAnalyzing.value = false
  }
}

function parseManualChapters() {
  const text = manualChaptersText.value.trim()
  if (!text) return []
  const chapters: { title: string; content: string }[] = []
  let title = i18n.t('admin_default_chapter_title')
  let lines: string[] = []

  const push = () => {
    const content = lines.join("\n").trim()
    if (content || chapters.length === 0) {
      chapters.push({ title, content })
    }
  }

  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^##\s+(.+)/)
    if (match) {
      push()
      title = match[1].trim()
      lines = []
    } else {
      lines.push(line)
    }
  }
  push()
  return chapters
}

async function submitManualBook() {
  manualError.value = ""
  manualResult.value = null
  if (!manualTitle.value.trim()) {
    manualError.value = i18n.t('admin_manual_title_required')
    return
  }
  const chapters = parseManualChapters()
  if (!chapters.length) {
    manualError.value = i18n.t('admin_manual_chapter_required')
    return
  }
  manualImporting.value = true
  try {
    manualResult.value = await api.post<any>("/books/manual", {
      title: manualTitle.value.trim(),
      author: manualAuthor.value.trim() || i18n.t('admin_unknown_author'),
      status: manualStatus.value,
      description: manualDescription.value.trim() || null,
      tags: manualTags.value.split(/[,，\s]+/).filter(Boolean),
      is_r18: classificationValue(manualClass.value),
      chapters,
    })
    manualTitle.value = ""
    manualAuthor.value = ""
    manualDescription.value = ""
    manualTags.value = ""
    manualChaptersText.value = ""
    manualStatus.value = "ongoing"
    manualAnalyzeResult.value = null
  } catch (e) {
    manualError.value = e instanceof Error ? e.message : i18n.t('admin_manual_upload_failed')
  } finally {
    manualImporting.value = false
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

async function loadIndexStats() {
  indexError.value = ""
  try {
    indexStats.value = await api.get<any>("/search/index/stats")
  } catch (e) {
    indexError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
    indexStats.value = null
  }
}

async function rebuildIndex() {
  indexRebuilding.value = true
  indexError.value = ""
  try {
    await api.post("/search/index/rebuild")
    await loadIndexStats()
  } catch (e) {
    indexError.value = e instanceof Error ? e.message : i18n.t('admin_rebuild_failed')
  } finally {
    indexRebuilding.value = false
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

const users = ref<any[]>([])
const userError = ref("")
const registrationApprovalEnabled = ref(false)
const userCreateForm = ref({ username: "", email: "", password: "", role: "user" })
const showUserCreate = ref(true)
const userCreating = ref(false)
const userCreateError = ref("")
const passwordChanging = ref<Record<string, boolean>>({})

const USERNAME_RE = /^[A-Za-z0-9]+$/

function passwordStrengthOk(value: string): boolean {
  let categories = 0
  if (/[A-Za-z]/.test(value)) categories++
  if (/\d/.test(value)) categories++
  if (value.includes("_")) categories++
  if (/[^A-Za-z0-9_]/.test(value)) categories++
  return categories >= 2
}

const approvals = ref<any[]>([])
const approvalError = ref("")
const approvalReviewing = ref<Record<string, boolean>>({})

async function loadUsers() {
  userError.value = ""
  try { users.value = await api.get<any[]>("/admin/users") } catch (e) { userError.value = e instanceof Error ? e.message : i18n.t('admin_failed') }
}

async function loadRegistrationApproval() {
  try {
    const res = await api.get<any>("/admin/settings/registration-approval")
    registrationApprovalEnabled.value = res.enabled
  } catch {
    registrationApprovalEnabled.value = false
  }
}

async function toggleRegistrationApproval() {
  try {
    const res = await api.put<any>("/admin/settings/registration-approval", {
      enabled: !registrationApprovalEnabled.value,
    })
    registrationApprovalEnabled.value = res.enabled
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('admin_failed'))
  }
}

async function createUser() {
  userCreateError.value = ""
  if (!userCreateForm.value.username.trim() || !userCreateForm.value.password) {
    userCreateError.value = i18n.t('admin_create_user_required')
    return
  }
  if (!USERNAME_RE.test(userCreateForm.value.username.trim())) {
    userCreateError.value = i18n.t('admin_username_invalid')
    return
  }
  if (!passwordStrengthOk(userCreateForm.value.password)) {
    userCreateError.value = i18n.t('admin_password_weak')
    return
  }
  userCreating.value = true
  try {
    const body: any = {
      username: userCreateForm.value.username.trim(),
      password: userCreateForm.value.password,
      role: userCreateForm.value.role,
    }
    if (userCreateForm.value.email.trim()) {
      body.email = userCreateForm.value.email.trim()
    }
    await api.post("/admin/users", body)
    await loadUsers()
    userCreateForm.value = { username: "", email: "", password: "", role: "user" }
  } catch (e) {
    userCreateError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  } finally {
    userCreating.value = false
  }
}

async function approveUser(id: string) {
  try {
    await api.put("/admin/users/" + id + "/approve")
    await loadUsers()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('admin_failed'))
  }
}

async function changeUserPassword(u: any) {
  const pwd = prompt(i18n.t('admin_password_prompt'))
  if (!pwd) return
  if (!passwordStrengthOk(pwd)) {
    alert(i18n.t('admin_password_weak'))
    return
  }
  passwordChanging.value[u.id] = true
  try {
    await api.put("/admin/users/" + u.id + "/password", { password: pwd })
    alert(i18n.t('admin_password_changed'))
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('admin_failed'))
  } finally {
    passwordChanging.value[u.id] = false
  }
}

async function deleteUser(id: string, username: string) {
  if (!confirm(i18n.t('admin_delete_user_confirm', { username }))) return
  try { await api.delete("/admin/users/" + id); await loadUsers() } catch (e) { alert(e instanceof Error ? e.message : i18n.t('admin_failed')) }
}

async function changeUserRole(id: string, role: string) {
  try { await api.put("/admin/users/" + id + "/role", { role }); await loadUsers() } catch (e) { alert(e instanceof Error ? e.message : i18n.t('admin_failed')) }
}

async function toggleUserVisibility(
  u: any,
  key: "r18_enabled" | "non_r18_enabled" | "can_manage_visibility",
) {
  try {
    const body: any = {}
    body[key] = !u[key]
    await api.put("/admin/users/" + u.id + "/visibility", body)
    await loadUsers()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('admin_failed'))
  }
}

async function loadApprovals() {
  approvalError.value = ""
  try { approvals.value = await api.get<any[]>("/source-changes?status=pending") } catch (e) { approvalError.value = e instanceof Error ? e.message : i18n.t('admin_failed') }
}

async function reviewChange(id: string, action: string) {
  approvalReviewing.value[id] = true
  try { await api.post("/source-changes/" + id + "/review", { action }); await loadApprovals(); await loadSources() } catch (e) { alert(e instanceof Error ? e.message : i18n.t('admin_failed')) }
  finally { approvalReviewing.value[id] = false }
}

onMounted(async () => {
  await loadSources()
  await loadPrefs()
  accountForm.value.nickname = auth.user?.nickname || ""
  await loadCreds()
  await loadCookies()
  if (auth.isAdmin) {
    await loadStatus()
    await loadIndexStats()
    await loadUsers()
    await loadRegistrationApproval()
    await loadApprovals()
    await loadLocalRoots()
    loadProxyConfig()
  }
  await loadTokens()
  await loadLogs()
  if (crawlStore.activeTask?.id && !["completed", "failed", "cancelled", "completed_with_errors"].includes(crawlStore.activeTask.status)) {
    crawlStore.startPolling(crawlStore.activeTask.id)
  }
})

onUnmounted(() => {
  // Polling lives in the global crawl store so the task survives route changes.
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

      <section v-if="tab === 'sources'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_add_source') }}</h2>
          <div class="grid grid-cols-2 gap-3 mb-3">
            <input v-model="sourceForm.id" :disabled="!!sourceEditingId" :placeholder="i18n.t('admin_placeholder_id')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 disabled:opacity-60" />
            <input v-model="sourceForm.name" :placeholder="i18n.t('admin_placeholder_name')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="sourceForm.url" :placeholder="i18n.t('admin_placeholder_url')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="sourceForm.plugin_name" :placeholder="i18n.t('admin_placeholder_plugin')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <div class="flex flex-wrap items-center gap-4 mb-3">
            <label v-if="auth.isAdmin" class="inline-flex items-center gap-2 text-xs text-muted dark:text-gray-400">
              <span>{{ i18n.t('admin_scope_label') }}</span>
              <select v-model="sourceForm.scope" class="px-2 py-1 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800">
                <option value="personal">{{ i18n.t('admin_source_personal') }}</option>
                <option value="global">{{ i18n.t('admin_source_global') }}</option>
              </select>
            </label>
            <label class="inline-flex items-center gap-2 text-xs text-muted dark:text-gray-400 cursor-pointer">
              <input type="checkbox" v-model="sourceForm.enabled" class="rounded" />
              {{ i18n.t('admin_source_enabled') }}
            </label>
            <label class="inline-flex items-center gap-2 text-xs text-muted dark:text-gray-400 cursor-pointer">
              <input type="checkbox" v-model="sourceForm.is_r18" class="rounded" />
              {{ i18n.t('admin_r18_label') }}
            </label>
          </div>
          <textarea
            v-model="sourceConfigText"
            :placeholder="i18n.t('admin_source_config_placeholder')"
            rows="6"
            class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs font-mono bg-paper dark:bg-gray-800 resize-y mb-3"
          />
          <p v-if="sourceError" class="text-sm text-red-600 mb-2">{{ sourceError }}</p>
          <div class="flex items-center gap-2">
            <button @click="createSource" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">
              {{ sourceEditingId ? i18n.t('admin_save') : i18n.t('admin_create_source') }}
            </button>
            <button v-if="sourceEditingId" @click="resetSourceForm" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-accent/5">{{ i18n.t('admin_cancel') }}</button>
          </div>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="s in sources" :key="s.id" class="px-4 py-3">
            <div class="flex items-center justify-between flex-wrap gap-2">
              <div>
                <span class="text-sm font-medium">{{ s.name }}</span>
                <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ s.id }} ({{ s.plugin_name }})</span>
                <span v-if="s.owner_id" class="text-xs px-1.5 py-0.5 rounded ml-2 bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300">{{ i18n.t('admin_source_personal') }}</span>
                <span v-else class="text-xs px-1.5 py-0.5 rounded ml-2 bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300">{{ i18n.t('admin_source_global') }}</span>
                <span v-if="s.is_r18" class="text-xs px-1.5 py-0.5 rounded ml-2 bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300">R18</span>
                <span
                  v-if="sourceCookies(s.id).length"
                  class="text-xs px-1.5 py-0.5 rounded ml-2"
                  :class="cookieExpired(sourceCookies(s.id)[0]) ? 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300' : 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'"
                >{{ cookieExpired(sourceCookies(s.id)[0]) ? i18n.t('admin_cookie_expired') : i18n.t('admin_cookie_saved') }}</span>
                <span v-else class="text-xs px-1.5 py-0.5 rounded ml-2 bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">{{ i18n.t('admin_cookie_not_saved') }}</span>
                <span
                  v-if="!s.owner_id && s.show_contributor && s.submitter_username"
                  class="text-xs text-muted dark:text-gray-400 ml-2"
                >{{ i18n.t('admin_source_contributor', { name: s.submitter_username }) }}</span>
              </div>
              <div class="flex items-center gap-3">
                <span class="text-xs" :class="s.enabled ? 'text-green-600' : 'text-red-500'">{{ s.enabled ? i18n.t('admin_enabled') : i18n.t('admin_disabled') }}</span>
                <button v-if="canManageSource(s)" @click="toggleSourceDetails(s)" class="text-xs text-accent hover:underline">
                  {{ i18n.t('admin_tab_cookies') }} / {{ i18n.t('admin_tab_creds') }}
                </button>
                <button v-if="canManageSource(s)" @click="editSource(s)" class="text-xs text-accent hover:underline">{{ i18n.t('admin_edit') }}</button>
                <button v-if="canManageSource(s)" @click="deleteSource(s.id)" class="text-xs text-red-500 hover:text-red-700">{{ i18n.t('admin_delete') }}</button>
              </div>
            </div>

            <div v-if="expandedSourceId === s.id && canManageSource(s)" class="mt-4 grid gap-4 lg:grid-cols-2">
              <div class="rounded-lg border border-border dark:border-gray-700 p-3">
                <h3 class="text-xs font-semibold mb-3">{{ i18n.t('admin_tab_cookies') }}</h3>
                <div v-if="sourceCookies(s.id).length" class="space-y-2 mb-3">
                  <div
                    v-for="c in sourceCookies(s.id)"
                    :key="c.id"
                    class="flex items-center justify-between gap-2 text-xs"
                  >
                    <span>{{ c.source }}</span>
                    <div class="flex items-center gap-2">
                      <button @click="editCookie(c)" class="text-accent hover:underline">{{ i18n.t('admin_edit') }}</button>
                      <button @click="deleteCookie(c.id)" class="text-red-500 hover:underline">{{ i18n.t('admin_delete') }}</button>
                    </div>
                  </div>
                </div>
                <p v-else class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_no_cookies') }}</p>
                <p
                  v-if="sourceCookies(s.id).length && cookieExpired(sourceCookies(s.id)[0])"
                  class="text-xs text-red-600 mb-2"
                >{{ i18n.t('admin_cookie_expired_hint') }}</p>
                <textarea
                  v-model="cookieForm.cookie_data"
                  :placeholder="i18n.t('admin_placeholder_cookie')"
                  rows="3"
                  class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs font-mono bg-paper dark:bg-gray-800 resize-y mb-2"
                />
                <input
                  v-model="cookieForm.expired_at"
                  type="datetime-local"
                  class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800 mb-2"
                />
                <div class="flex gap-2">
                  <button @click="saveSourceCookie(s)" class="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium">
                    {{ sourceCookies(s.id).length ? i18n.t('admin_update_cookie') : i18n.t('admin_save_cookie') }}
                  </button>
                  <button @click="testSourceCookie(s)" :disabled="cookieTesting" class="px-3 py-1.5 rounded border border-accent text-accent text-xs font-medium disabled:opacity-50">
                    {{ cookieTesting ? i18n.t('admin_testing') : i18n.t('admin_test_cookie') }}
                  </button>
                </div>
                <p v-if="cookieError" class="text-xs text-red-600 mt-2">{{ cookieError }}</p>
                <div v-if="cookieTestResult" class="mt-2 text-xs text-green-700 dark:text-green-400">{{ cookieTestResult.message }}</div>
                <p v-if="cookieTestError" class="text-xs text-red-600 mt-2">{{ cookieTestError }}</p>
              </div>

              <div class="rounded-lg border border-border dark:border-gray-700 p-3">
                <h3 class="text-xs font-semibold mb-3">{{ i18n.t('admin_tab_creds') }}</h3>
                <div v-if="sourceCreds(s.id).length" class="space-y-2 mb-3">
                  <div
                    v-for="c in sourceCreds(s.id)"
                    :key="c.id"
                    class="flex items-center justify-between gap-2 text-xs"
                  >
                    <span>{{ c.username }}</span>
                    <div class="flex items-center gap-2">
                      <button @click="autoLogin(c.id)" :disabled="credLoggingIn[c.id]" class="text-green-600 hover:underline disabled:opacity-50">
                        {{ credLoggingIn[c.id] ? i18n.t('admin_logging_in') : i18n.t('admin_auto_login') }}
                      </button>
                      <button @click="startManualLogin(c.id)" :disabled="manualLoginLoading" class="text-blue-600 hover:underline disabled:opacity-50">
                        {{ i18n.t('admin_manual_login') }}
                      </button>
                      <button @click="deleteCred(c.id)" class="text-red-500 hover:underline">{{ i18n.t('admin_delete') }}</button>
                    </div>
                    <p v-if="credLoginResult[c.id]" class="w-full text-green-700 dark:text-green-400">{{ credLoginResult[c.id].message }}</p>
                    <p v-if="credLoginError[c.id]" class="w-full text-red-600">{{ credLoginError[c.id] }}</p>
                  </div>
                </div>
                <p v-else class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_no_creds') }}</p>
                <input
                  v-model="credForm.username"
                  :placeholder="i18n.t('admin_placeholder_username')"
                  class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800 mb-2"
                />
                <input
                  v-model="credForm.password"
                  type="password"
                  :placeholder="i18n.t('admin_placeholder_password')"
                  class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800 mb-2"
                />
                <button @click="saveSourceCred(s)" class="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium">{{ i18n.t('admin_save_cred') }}</button>
                <p v-if="credError" class="text-xs text-red-600 mt-2">{{ credError }}</p>
              </div>
            </div>
          </div>
          <p v-if="sources.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_sources') }}</p>
        </div>
      </section>

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
            <input v-model="cookieForm.source" :placeholder="i18n.t('admin_placeholder_source')" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="cookieForm.expired_at" type="datetime-local" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <textarea v-model="cookieForm.cookie_data" :placeholder="i18n.t('admin_placeholder_cookie')" rows="3" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y" />
          </div>
          <p v-if="cookieError" class="text-sm text-red-600 mb-2">{{ cookieError }}</p>
          <div class="flex gap-3">
            <button @click="createCookie" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">{{ i18n.t('admin_save_cookie') }}</button>
            <button @click="testCookie" :disabled="cookieTesting" class="px-4 py-2 rounded border border-accent text-accent text-sm font-medium hover:bg-accent/10 disabled:opacity-50">
              {{ cookieTesting ? i18n.t('admin_testing') : i18n.t('admin_test_cookie') }}
            </button>
            <button v-if="cookieEditingId" @click="cookieEditingId = ''; cookieForm = { source: '', cookie_data: '', expired_at: '' }" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-accent/5">{{ i18n.t('admin_cancel') }}</button>
          </div>
          <div v-if="cookieTestResult" class="mt-3 p-3 rounded bg-green-50 dark:bg-green-950 text-sm">
            <p class="font-medium text-green-700 dark:text-green-400">{{ cookieTestResult.message }}</p>
            <div v-if="cookieTestResult.sample_books" class="mt-2 space-y-1 text-xs text-muted dark:text-gray-400">
              <p v-for="b in cookieTestResult.sample_books" :key="b.title">{{ b.title }} &mdash; {{ b.author }}</p>
            </div>
          </div>
          <p v-if="cookieTestError" class="text-sm text-red-600 mt-2">{{ cookieTestError }}</p>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="c in cookies" :key="c.id" class="px-4 py-3 flex items-center justify-between">
            <div class="flex items-center gap-3">
              <span class="text-sm font-medium">{{ c.source }}</span>
              <span v-if="c.expired_at" class="text-xs px-1.5 py-0.5 rounded-full" :class="new Date(c.expired_at) < new Date() ? 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300' : new Date(c.expired_at) < new Date(Date.now() + 3*86400000) ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300' : 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'">
                {{ new Date(c.expired_at) < new Date() ? i18n.t('admin_cookie_expired') : new Date(c.expired_at) < new Date(Date.now() + 3*86400000) ? i18n.t('admin_cookie_expiring') : i18n.t('admin_cookie_valid') }}
              </span>
            </div>
            <div class="flex items-center gap-3">
              <button @click="editCookie(c)" class="text-xs text-accent hover:underline">{{ i18n.t('admin_edit') }}</button>
              <button @click="deleteCookie(c.id)" class="text-xs text-red-500 hover:text-red-700">{{ i18n.t('admin_delete') }}</button>
            </div>
          </div>
          <p v-if="cookies.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_cookies') }}</p>
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
          <button
            @click="rebuildIndex"
            :disabled="indexRebuilding"
            class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >{{ indexRebuilding ? i18n.t('admin_index_rebuilding') : i18n.t('admin_index_rebuild') }}</button>
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

      <section v-if="tab === 'add'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 max-w-3xl">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_local_markdown') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_local_hint') }}</p>
          <div class="flex flex-col sm:flex-row gap-2">
            <input
              v-model="localPath"
              :placeholder="i18n.t('admin_local_path_placeholder')"
              list="local-import-roots"
              class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
            />
            <datalist id="local-import-roots">
              <option v-for="root in localRoots" :key="root.path" :value="root.path">{{ root.name }}</option>
            </datalist>
            <button
              type="button"
              @click="openLocalBrowser()"
              class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-accent/5"
            >{{ i18n.t('admin_local_choose_dir') }}</button>
          </div>
          <p v-if="localError" class="text-sm text-red-600 mt-2">{{ localError }}</p>
          <div class="mt-3 flex flex-wrap items-center gap-2">
            <label class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_import_classification') }}</label>
            <select
              v-model="localClass"
              class="px-2 py-1.5 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800"
            >
              <option value="auto">{{ i18n.t('admin_classification_auto') }}</option>
              <option value="all">{{ i18n.t('admin_classification_all_ages') }}</option>
              <option value="r18">{{ i18n.t('admin_classification_r18') }}</option>
            </select>
          </div>
          <div class="mt-3 flex flex-wrap gap-2">
            <button
              @click="scanLocal"
              :disabled="localScanning"
              class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
            >{{ localScanning ? i18n.t('admin_importing_short') : i18n.t('admin_local_scan') }}</button>
            <button
              @click="importLocal"
              :disabled="localImporting"
              class="px-4 py-2 rounded border border-accent text-accent text-sm font-medium hover:bg-accent/10 disabled:opacity-50"
            >{{ localImporting ? i18n.t('admin_importing_short') : i18n.t('admin_local_import') }}</button>
          </div>
          <p v-if="localScan.length" class="text-xs text-muted dark:text-gray-400 mt-3">
            {{ i18n.t('admin_local_scan_count', { n: localScan.length }) }}: {{ localScanRoot }}
          </p>
          <div v-if="localScan.length" class="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <label class="inline-flex items-center gap-1.5 cursor-pointer select-none">
              <input
                type="checkbox"
                :checked="allLocalSelected"
                @change="toggleAllLocalBooks"
                class="rounded"
              />
              <span>{{ i18n.t('admin_local_select_all') }}</span>
            </label>
            <button
              type="button"
              @click="invertLocalBooks"
              class="px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5"
            >{{ i18n.t('admin_local_select_invert') }}</button>
            <button
              type="button"
              @click="clearLocalBooks"
              class="px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5"
            >{{ i18n.t('admin_local_select_none') }}</button>
            <span class="text-muted dark:text-gray-400">{{ i18n.t('admin_local_selected_count', { n: localSelected.length }) }}</span>
          </div>
          <div v-if="localScan.length" class="mt-3 max-h-64 overflow-y-auto divide-y divide-border border border-border dark:border-gray-700 rounded">
            <label
              v-for="book in localScan"
              :key="book.path"
              class="flex items-start gap-2 px-3 py-2 cursor-pointer hover:bg-accent/5"
            >
              <input
                type="checkbox"
                :checked="localSelected.includes(book.path)"
                @change="toggleLocalBook(book.path)"
                class="mt-1 rounded"
              />
              <span class="min-w-0">
                <span class="block text-sm font-medium truncate">{{ book.title }}</span>
                <span class="block text-xs text-muted dark:text-gray-400 truncate">
                  {{ book.author }} · {{ book.chapter_count }} chapters · {{ book.format }}
                  <span v-if="book.is_r18" class="ml-1 px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">R18</span>
                  <span v-if="book.categories?.length" class="ml-1">{{ book.categories.join(' / ') }}</span>
                  <span v-if="book.tags?.length" class="ml-1">{{ book.tags.slice(0, 6).join(', ') }}</span>
                </span>
                <span class="block text-xs text-muted dark:text-gray-400 truncate">{{ book.path }}</span>
              </span>
            </label>
          </div>
          <div v-if="localScan.length" class="mt-3 flex flex-wrap gap-2">
            <button
              @click="importLocalAll"
              :disabled="localImporting || localScan.length === 0"
              class="px-4 py-2 rounded border border-accent text-accent text-sm font-medium hover:bg-accent/10 disabled:opacity-50"
            >{{ i18n.t('admin_local_import_all') }}</button>
            <button
              @click="importLocalSelected"
              :disabled="localImporting || localSelected.length === 0"
              class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
            >{{ i18n.t('admin_local_import_selected') }}</button>
            <button
              @click="directLocalSelected"
              :disabled="localImporting || localSelected.length === 0"
              class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-accent/5 disabled:opacity-50"
            >{{ i18n.t('admin_local_direct') }}</button>
          </div>
          <div v-if="localResult" class="mt-3 p-3 rounded bg-green-50 dark:bg-green-950 text-sm">
            <p v-if="localResult.book_id">{{ i18n.t('admin_book_id') }}: {{ localResult.book_id }}</p>
            <p v-for="item in localResult.results || []" :key="item.path">
              {{ item.path }}: {{ item.status }} {{ item.book_id || item.error || '' }}
            </p>
          </div>
          <div v-if="localDirectResult" class="mt-3 p-3 rounded bg-blue-50 dark:bg-blue-950 text-sm">
            <p v-for="book in localDirectResult.books || []" :key="book.path">
              {{ book.title }} · {{ book.chapter_count }} chapters · {{ book.path }}
              <span v-if="book.is_r18" class="ml-1 px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">R18</span>
              <span v-if="book.categories?.length" class="ml-1">{{ book.categories.join(' / ') }}</span>
              <span v-if="book.tags?.length" class="ml-1">{{ book.tags.slice(0, 6).join(', ') }}</span>
            </p>
          </div>
        </div>

        <div v-if="localBrowserOpen" class="fixed inset-0 z-[90] bg-black/50 flex items-center justify-center p-4" @click.self="localBrowserOpen = false">
          <div class="w-full max-w-lg rounded-lg border border-border bg-surface dark:bg-gray-900 p-4 shadow-xl">
            <div class="flex items-center justify-between gap-3 mb-3">
              <span class="text-sm font-medium truncate">{{ localBrowserName || localBrowserPath || i18n.t('admin_local_browse_title') }}</span>
              <button
                type="button"
                class="w-8 h-8 shrink-0 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10"
                @click="localBrowserOpen = false"
              >×</button>
            </div>
            <div class="flex flex-wrap items-center gap-2 mb-3">
              <button
                v-for="root in localRoots"
                :key="root.path"
                type="button"
                class="px-2 py-1 rounded border border-border dark:border-gray-700 text-xs hover:bg-accent/5"
                @click="browseLocalDirectory(root.path)"
              >{{ root.name }}</button>
              <button
                v-if="localBrowserParent"
                type="button"
                class="px-2 py-1 rounded border border-border dark:border-gray-700 text-xs hover:bg-accent/5"
                @click="browseLocalDirectory(localBrowserParent)"
              >{{ i18n.t('admin_local_browse_up') }}</button>
              <button
                v-if="localBrowserPath"
                type="button"
                class="px-2 py-1 rounded bg-accent text-white text-xs hover:opacity-90"
                @click="chooseLocalDirectory(localBrowserPath)"
              >{{ i18n.t('admin_local_browse_choose') }}</button>
            </div>
            <div class="max-h-64 overflow-y-auto divide-y divide-border border border-border dark:border-gray-700 rounded">
              <button
                v-for="dir in localBrowserDirs"
                :key="dir.path"
                type="button"
                class="w-full text-left px-3 py-2 hover:bg-accent/5"
                @click="browseLocalDirectory(dir.path)"
              >
                <span class="block text-sm font-medium truncate">{{ dir.name }}</span>
                <span class="block text-xs text-muted dark:text-gray-400 truncate">{{ dir.path }}</span>
              </button>
              <p v-if="!localBrowsing && localBrowserDirs.length === 0" class="px-3 py-4 text-sm text-muted dark:text-gray-400">
                {{ i18n.t('admin_local_browse_empty') }}
              </p>
              <p v-if="localBrowsing" class="px-3 py-4 text-sm text-muted dark:text-gray-400">
                {{ i18n.t('admin_local_browse_loading') }}
              </p>
            </div>
          </div>
        </div>

        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 max-w-3xl">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_manual_upload') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3" v-html="i18n.t('admin_manual_hint')"></p>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <input v-model="manualTitle" :placeholder="i18n.t('admin_manual_title_placeholder')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="manualAuthor" :placeholder="i18n.t('admin_manual_author_placeholder')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
            <select v-model="manualStatus" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
              <option value="ongoing">{{ i18n.t('admin_manual_status_ongoing') }}</option>
              <option value="completed">{{ i18n.t('admin_manual_status_completed') }}</option>
            </select>
            <input v-model="manualTags" :placeholder="i18n.t('admin_manual_tags_placeholder')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <select v-model="manualClass" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
              <option value="auto">{{ i18n.t('admin_classification_auto') }}</option>
              <option value="all">{{ i18n.t('admin_classification_all_ages') }}</option>
              <option value="r18">{{ i18n.t('admin_classification_r18') }}</option>
            </select>
          </div>
          <textarea
            v-model="manualDescription"
            rows="3"
            :placeholder="i18n.t('admin_manual_desc_placeholder')"
            class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y mb-3"
          ></textarea>
          <textarea
            v-model="manualChaptersText"
            rows="10"
            :placeholder="i18n.t('admin_manual_chapters_placeholder')"
            class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y font-mono mb-3"
          ></textarea>

          <div class="flex items-center gap-3 mb-3">
            <button
              type="button"
              @click="analyzeManualText()"
              :disabled="manualAnalyzing || !manualChaptersText.trim()"
              class="px-3 py-2 rounded border border-accent text-accent text-sm hover:bg-accent/10 disabled:opacity-50"
            >{{ manualAnalyzing ? i18n.t('admin_manual_analyzing') : i18n.t('admin_manual_auto_analyze') }}</button>
            <label class="inline-flex px-3 py-2 rounded border border-border dark:border-gray-700 text-sm cursor-pointer hover:bg-accent/5">
              {{ i18n.t('admin_manual_pick_file') }}
              <input type="file" accept=".txt,.md,text/plain,text/markdown" class="hidden" @change="onManualFile" />
            </label>
            <span class="text-xs text-muted dark:text-gray-400" v-html="i18n.t('admin_manual_chapter_hint')"></span>
          </div>
          <div v-if="manualAnalyzeResult" class="mb-3 p-3 rounded bg-blue-50 dark:bg-blue-950 text-sm flex flex-wrap items-center gap-2">
            <span
              :class="manualAnalyzeResult.is_r18
                ? 'px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300'
                : 'px-1.5 py-0.5 rounded bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'"
            >{{ manualAnalyzeResult.is_r18 ? 'R18' : 'All-Ages' }}</span>
            <span v-if="manualAnalyzeResult.categories?.length" class="text-muted dark:text-gray-400">
              {{ manualAnalyzeResult.categories.join(' / ') }}
            </span>
            <span v-if="manualAnalyzeResult.tags?.length" class="text-muted dark:text-gray-400">
              {{ manualAnalyzeResult.tags.slice(0, 8).join(', ') }}
            </span>
          </div>

          <p v-if="manualError" class="text-sm text-red-600 mb-2">{{ manualError }}</p>
          <button
            @click="submitManualBook"
            :disabled="manualImporting"
            class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >{{ manualImporting ? i18n.t('admin_saving') : i18n.t('admin_save_book') }}</button>
          <div v-if="manualResult" class="mt-3 p-3 rounded bg-green-50 dark:bg-green-950 text-sm">
            <p>{{ i18n.t('admin_book_id') }}: {{ manualResult.book_id }}</p>
            <p>{{ i18n.t('admin_created_chapters_label') }}: {{ manualResult.created_chapters }}</p>
            <p v-if="manualResult.is_r18" class="mt-1 px-1.5 py-0.5 inline-block rounded bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">R18</p>
            <p v-if="manualResult.category_names?.length" class="mt-1 text-muted dark:text-gray-400">
              {{ manualResult.category_names.join(' / ') }}
            </p>
            <p v-if="manualResult.tags?.length" class="mt-1 text-muted dark:text-gray-400">
              {{ manualResult.tags.slice(0, 8).join(', ') }}
            </p>
          </div>
        </div>

      </section>

      <section v-if="tab === 'creds'" class="space-y-6">
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_add_cred') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_cred_hint') }}</p>
          <div class="grid grid-cols-3 gap-3 mb-3">
            <input v-model="credForm.source" :placeholder="i18n.t('admin_placeholder_source')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="credForm.username" :placeholder="i18n.t('admin_placeholder_username')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="credForm.password" type="password" :placeholder="i18n.t('admin_placeholder_password')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <p v-if="credError" class="text-sm text-red-600 mb-2">{{ credError }}</p>
          <button @click="createCred" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">{{ i18n.t('admin_save_cred') }}</button>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="c in creds" :key="c.id" class="px-4 py-3 flex items-center justify-between flex-wrap gap-2">
            <div>
              <span class="text-sm font-medium">{{ c.source }}</span>
              <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ c.username }}</span>
            </div>
            <div class="flex items-center gap-3">
              <button @click="autoLogin(c.id)" :disabled="credLoggingIn[c.id]" class="px-3 py-1.5 rounded border border-green-500 text-green-600 text-xs font-medium hover:bg-green-50 dark:hover:bg-green-950 disabled:opacity-50">
                {{ credLoggingIn[c.id] ? i18n.t('admin_logging_in') : i18n.t('admin_auto_login') }}
              </button>
              <button @click="startManualLogin(c.id)" :disabled="manualLoginLoading" class="px-3 py-1.5 rounded border border-blue-500 text-blue-600 text-xs font-medium hover:bg-blue-50 dark:hover:bg-blue-950 disabled:opacity-50">
                {{ i18n.t('admin_manual_login') }}
              </button>
              <button @click="deleteCred(c.id)" class="text-xs text-red-500 hover:text-red-700">{{ i18n.t('admin_delete') }}</button>
            </div>
            <div v-if="credLoginResult[c.id]" class="w-full mt-1 p-2 rounded bg-green-50 dark:bg-green-950 text-xs text-green-700 dark:text-green-400">
              {{ credLoginResult[c.id].message }}
            </div>
            <p v-if="credLoginError[c.id]" class="w-full text-xs text-red-600 mt-1">{{ credLoginError[c.id] }}</p>
          </div>
          <p v-if="creds.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_creds') }}</p>
        </div>
      </section>
      <section v-if="tab === 'users'" class="space-y-6">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-semibold">{{ i18n.t('admin_user_management') }}</h2>
          <div class="flex items-center gap-2">
            <button
              @click="showUserCreate = !showUserCreate"
              class="px-4 py-2 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity"
            >{{ i18n.t('admin_create_user') }}</button>
            <button @click="loadUsers" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-surface transition-colors">{{ i18n.t('admin_refresh') }}</button>
          </div>
        </div>
        <p v-if="userError" class="text-sm text-red-600 mb-3">{{ userError }}</p>
        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <div class="flex items-center justify-between flex-wrap gap-3">
            <div>
              <h3 class="text-sm font-semibold mb-1">{{ i18n.t('admin_registration_approval') }}</h3>
              <p class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_registration_approval_hint') }}</p>
            </div>
            <button
              @click="toggleRegistrationApproval"
              class="text-xs px-3 py-1.5 rounded border"
              :class="registrationApprovalEnabled ? 'bg-green-100 text-green-700 border-green-400 dark:bg-green-900 dark:text-green-300' : 'border-border dark:border-gray-700 text-muted dark:text-gray-400'"
            >{{ registrationApprovalEnabled ? i18n.t('admin_enabled') : i18n.t('admin_disabled') }}</button>
          </div>
        </div>

        <div v-if="showUserCreate" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
          <h3 class="text-sm font-semibold mb-3">{{ i18n.t('admin_create_user') }}</h3>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <input
              v-model="userCreateForm.username"
              :placeholder="i18n.t('admin_username_placeholder')"
              class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
            />
            <input
              v-model="userCreateForm.email"
              type="email"
              :placeholder="i18n.t('admin_email_placeholder')"
              class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
            />
            <input
              v-model="userCreateForm.password"
              type="password"
              :placeholder="i18n.t('admin_password_placeholder')"
              class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
            />
            <select
              v-model="userCreateForm.role"
              class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
            >
              <option value="user">{{ i18n.t('admin_role_user') }}</option>
              <option value="admin">{{ i18n.t('admin_role_admin') }}</option>
              <option value="super_admin">{{ i18n.t('admin_role_super_admin') }}</option>
            </select>
          </div>
          <p v-if="userCreateError" class="text-sm text-red-600 mt-2">{{ userCreateError }}</p>
          <button
            @click="createUser"
            :disabled="userCreating"
            class="mt-3 px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >{{ userCreating ? i18n.t('admin_saving') : i18n.t('admin_create_user') }}</button>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="u in users" :key="u.id" class="px-4 py-3 flex items-center justify-between flex-wrap gap-2">
            <div>
              <span class="text-sm font-medium">{{ u.username }}</span>
              <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ u.email || '' }}</span>
              <span class="text-xs px-1.5 py-0.5 rounded-full ml-2" :class="u.role === 'super_admin' ? 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300' : u.role === 'admin' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'">{{ u.role === 'super_admin' ? i18n.t('admin_role_super_admin') : u.role === 'admin' ? i18n.t('admin_role_admin') : i18n.t('admin_role_user') }}</span>
              <span v-if="!u.approved" class="text-xs px-1.5 py-0.5 rounded-full ml-2 bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300">{{ i18n.t('admin_pending') }}</span>
              <span v-if="auth.isSuperAdmin && u.invite_tag" class="text-xs px-1.5 py-0.5 rounded-full ml-2 bg-cyan-100 text-cyan-700 dark:bg-cyan-900 dark:text-cyan-300">{{ i18n.t('admin_invite_tag', { tag: u.invite_tag }) }}</span>
            </div>
            <div class="flex items-center gap-2">
              <button @click="toggleUserVisibility(u, 'r18_enabled')" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5" :class="u.r18_enabled ? 'text-purple-700 dark:text-purple-300 border-purple-500' : ''">
                {{ u.r18_enabled ? i18n.t('admin_r18_on') : i18n.t('admin_r18_off') }}
              </button>
              <button @click="toggleUserVisibility(u, 'non_r18_enabled')" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5" :class="u.non_r18_enabled ? 'text-green-700 dark:text-green-300 border-green-500' : ''">
                {{ u.non_r18_enabled ? i18n.t('admin_all_ages_on') : i18n.t('admin_all_ages_off') }}
              </button>
              <button @click="toggleUserVisibility(u, 'can_manage_visibility')" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5" :class="u.can_manage_visibility ? 'text-blue-700 dark:text-blue-300 border-blue-500' : ''">
                {{ u.can_manage_visibility ? i18n.t('admin_controls_on') : i18n.t('admin_controls_off') }}
              </button>
              <button v-if="!u.approved" @click="approveUser(u.id)" class="text-xs px-2 py-1 rounded border border-green-500 text-green-600 hover:bg-green-50 dark:hover:bg-green-950">
                {{ i18n.t('admin_approve_user') }}
              </button>
              <button v-if="auth.isSuperAdmin" @click="changeUserPassword(u)" :disabled="passwordChanging[u.id]" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5 disabled:opacity-50">
                {{ i18n.t('admin_change_password') }}
              </button>
              <select @change="(e: any) => changeUserRole(u.id, e.target.value)" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 bg-paper dark:bg-gray-800">
                <option value="" disabled selected>{{ i18n.t('admin_change_role') }}</option>
                <option value="user">{{ i18n.t('admin_role_user') }}</option>
                <option value="admin">{{ i18n.t('admin_role_admin') }}</option>
                <option value="super_admin">{{ i18n.t('admin_role_super_admin') }}</option>
              </select>
              <button @click="deleteUser(u.id, u.username)" class="text-xs text-red-500 hover:text-red-700">{{ u.approved ? i18n.t('admin_delete') : i18n.t('admin_reject_user') }}</button>
            </div>
          </div>
          <p v-if="users.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_users') }}</p>
        </div>
      </section>

      <section v-if="tab === 'approvals'" class="space-y-6">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-semibold">{{ i18n.t('admin_pending_approvals') }}</h2>
          <button @click="loadApprovals" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-surface transition-colors">{{ i18n.t('admin_refresh') }}</button>
        </div>
        <p class="text-xs text-muted dark:text-gray-400 -mt-3 mb-3">{{ i18n.t('admin_approval_sync_hint') }}</p>
        <p v-if="approvalError" class="text-sm text-red-600 mb-3">{{ approvalError }}</p>
        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="a in approvals" :key="a.id" class="px-4 py-3 flex items-center justify-between flex-wrap gap-2">
            <div>
              <span class="text-xs px-1.5 py-0.5 rounded-full mr-2" :class="a.action === 'create' ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' : a.action === 'confirm_r18' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300' : 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300'">{{ a.action === 'confirm_r18' ? i18n.t('admin_approval_r18_conflict') : a.action }}</span>
              <template v-if="a.action === 'create' && a.source_data">
                <span class="text-sm font-medium">{{ a.source_data.name }}</span>
                <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ a.source_data.id }} ({{ a.source_data.plugin_name }})</span>
              </template>
              <template v-else-if="a.source_id">
                <span class="text-sm font-medium">{{ i18n.t('admin_delete_source', { id: a.source_id }) }}</span>
              </template>
              <template v-else-if="a.action === 'confirm_r18' && a.source_data">
                <span class="text-sm font-medium">{{ i18n.t('admin_approval_r18_conflict') }}</span>
                <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ a.source_data.title }}</span>
              </template>
              <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ i18n.t('admin_by_user', { id: a.submitter_username || a.user_id?.slice(0, 8) }) }}...</span>
            </div>
            <div class="flex items-center gap-2">
              <button @click="reviewChange(a.id, 'approve')" :disabled="approvalReviewing[a.id]" class="px-3 py-1 rounded bg-green-600 text-white text-xs font-medium hover:bg-green-700 disabled:opacity-50">{{ i18n.t('admin_approve') }}</button>
              <button @click="reviewChange(a.id, 'reject')" :disabled="approvalReviewing[a.id]" class="px-3 py-1 rounded bg-red-500 text-white text-xs font-medium hover:bg-red-600 disabled:opacity-50">{{ i18n.t('admin_reject') }}</button>
            </div>
          </div>
          <p v-if="approvals.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_approvals') }}</p>
        </div>
      </section>

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
    </main>
  </div>
</template>
