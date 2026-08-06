<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue"
import { useRouter } from "vue-router"
import { api } from "../api/client"
import { useI18nStore } from "../stores/i18n"
import { useCrawlStore } from "../stores/crawl"
import NavBar from "../components/NavBar.vue"

const i18n = useI18nStore()
const crawlStore = useCrawlStore()
const router = useRouter()

interface Source {
  id: string
  name: string
  url: string | null
  plugin_name: string
  enabled: boolean
  is_r18: boolean
}

interface CookieItem {
  id: string
  source: string
  cookie_data: string
  expired_at: string | null
}

const tab = ref<"sources" | "cookies" | "sync" | "logs" | "tokens" | "index" | "status" | "yuedu" | "add" | "creds" | "users" | "approvals" | "proxy">("yuedu")

const sources = ref<Source[]>([])
const sourceForm = ref({ id: "", name: "", url: "", plugin_name: "alicesw", is_r18: false })
const sourceError = ref("")

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
async function createSource() {
  sourceError.value = ""
  try {
    await api.post("/sources", sourceForm.value)
    await loadSources()
    sourceForm.value = { id: "", name: "", url: "", plugin_name: "alicesw", is_r18: false }
  } catch (e) {
    sourceError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
  }
}

const cookies = ref<CookieItem[]>([])
const cookieForm = ref({ source: "", cookie_data: "", expired_at: "" })
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
    await api.post("/cookies", body)
    await loadCookies()
    cookieForm.value = { source: "", cookie_data: "", expired_at: "" }
  } catch (e) {
    cookieError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
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

const crawlAllSourceId = ref("")
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
const yueduSyncResult = ref<any>(null)
const yueduSyncError = ref("")

const localPath = ref("")
const localImporting = ref(false)
const localResult = ref<any>(null)
const localError = ref("")
const localScan = ref<any[]>([])
const localScanRoot = ref("")
const localSelected = ref<string[]>([])
const localScanning = ref(false)
const localDirectResult = ref<any>(null)

const manualTitle = ref("")
const manualAuthor = ref("")
const manualStatus = ref("ongoing")
const manualDescription = ref("")
const manualTags = ref("")
const manualChaptersText = ref("")
const manualImporting = ref(false)
const manualResult = ref<any>(null)
const manualError = ref("")

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
    yueduResult.value = await api.post("/yuedu/import", body)
    await loadSources()
    await loadCreds()
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
    })
    await loadSources()
  } catch (e) {
    localError.value = e instanceof Error ? e.message : i18n.t('admin_local_import_failed')
  } finally {
    localImporting.value = false
  }
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
    })
  } catch (e) {
    localError.value = e instanceof Error ? e.message : i18n.t('admin_local_import_failed')
  } finally {
    localImporting.value = false
  }
}

function onManualFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  file.text().then((text) => {
    manualChaptersText.value = text
  })
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
      chapters,
    })
    manualTitle.value = ""
    manualAuthor.value = ""
    manualDescription.value = ""
    manualTags.value = ""
    manualChaptersText.value = ""
    manualStatus.value = "ongoing"
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
  logs.value = await api.get<any[]>("/crawl/tasks")
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
  await loadCreds()
  await loadCookies()
  await loadTokens()
  await loadStatus()
  await loadIndexStats()
  await loadUsers()
  await loadRegistrationApproval()
  await loadApprovals()
  await loadLogs()
  if (crawlStore.activeTask?.id && !["completed", "failed", "cancelled", "completed_with_errors"].includes(crawlStore.activeTask.status)) {
    crawlStore.startPolling(crawlStore.activeTask.id)
  }
  loadProxyConfig()
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
          v-for="t in (['sources', 'cookies', 'sync', 'logs', 'tokens', 'index', 'status', 'yuedu', 'add', 'creds', 'users', 'approvals', 'proxy'] as const)"
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
          <div class="flex items-center gap-2 mb-3">
            <input type="checkbox" id="source-r18" v-model="sourceForm.is_r18" class="rounded" />
            <label for="source-r18" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_r18_label') }}</label>
          </div>
          <p v-if="sourceError" class="text-sm text-red-600 mb-2">{{ sourceError }}</p>
          <button @click="createSource" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">{{ i18n.t('admin_create_source') }}</button>
        </div>

        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="s in sources" :key="s.id" class="px-4 py-3 flex items-center justify-between">
            <div>
              <span class="text-sm font-medium">{{ s.name }}</span>
              <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ s.id }} ({{ s.plugin_name }})</span>
              <span v-if="s.is_r18" class="text-xs px-1.5 py-0.5 rounded ml-2 bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300">R18</span>
            </div>
            <div class="flex items-center gap-3"><span class="text-xs" :class="s.enabled ? 'text-green-600' : 'text-red-500'">{{ s.enabled ? i18n.t('admin_enabled') : i18n.t('admin_disabled') }}</span><button @click="deleteSource(s.id)" class="text-xs text-red-500 hover:text-red-700">{{ i18n.t('admin_delete') }}</button></div>
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
            <p>{{ i18n.t('admin_created_chapters') }}: {{ syncResult.created_chapters }} / {{ i18n.t('admin_skipped') }}: {{ syncResult.skipped_chapters }}<span v-if="syncResult.failed_chapters?.length"> / {{ i18n.t('admin_failed') }}: {{ syncResult.failed_chapters.length }}</span></p>
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

        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mt-4">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_full_site_sync') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_full_site_hint') }}</p>
          <div class="flex gap-3 mb-3">
            <input v-model="crawlAllSourceId" :placeholder="i18n.t('admin_placeholder_source')" class="flex-1 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
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
              <label class="block text-xs font-medium mb-1.5">{{ i18n.t('admin_yuedu_cookie_label') }}</label>
              <textarea v-model="yueduCookie" :placeholder="i18n.t('admin_yuedu_cookie_placeholder')" rows="3" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y font-mono text-xs" />
            </div>
            <div class="flex items-center gap-2">
              <input type="checkbox" id="yuedu-discover" v-model="yueduDiscover" class="rounded" />
              <label for="yuedu-discover" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_yuedu_discover_label') }}</label>
            </div>
            <div class="flex items-center gap-2">
              <input type="checkbox" id="yuedu-r18" v-model="yueduIsR18" class="rounded" />
              <label for="yuedu-r18" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_r18_label') }}</label>
            </div>

            <p v-if="yueduSyncError" class="text-sm text-red-600">{{ yueduSyncError }}</p>

            <button @click="yueduImportAndSync" :disabled="yueduSyncImporting"
              class="w-full py-3 rounded-lg bg-accent text-white font-semibold hover:opacity-90 disabled:opacity-50 transition-all text-sm">
              {{ yueduSyncImporting ? i18n.t('admin_yuedu_importing_sync') : i18n.t('admin_yuedu_import_sync_btn') }}
            </button>

            <div v-if="yueduSyncResult" class="mt-4 space-y-3">
              <div class="grid grid-cols-4 gap-3 text-center">
                <div class="p-3 rounded bg-green-50 dark:bg-green-950">
                  <div class="text-xl font-bold text-green-700 dark:text-green-400">{{ yueduSyncResult.sources_imported }}</div>
                  <div class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_yuedu_sources_imported') }}</div>
                </div>
                <div class="p-3 rounded bg-blue-50 dark:bg-blue-950">
                  <div class="text-xl font-bold text-blue-700 dark:text-blue-400">{{ yueduSyncResult.books_synced }}</div>
                  <div class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_yuedu_bookshelf_synced') }}</div>
                </div>
                <div class="p-3 rounded bg-purple-50 dark:bg-purple-950">
                  <div class="text-xl font-bold text-purple-700 dark:text-purple-400">{{ yueduSyncResult.chapters_downloaded }}</div>
                  <div class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_yuedu_chapters_downloaded') }}</div>
                </div>
                <div class="p-3 rounded bg-amber-50 dark:bg-amber-950">
                  <div class="text-xl font-bold text-amber-700 dark:text-amber-400">{{ yueduSyncResult.books_discovered }}</div>
                  <div class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_yuedu_novels_discovered') }}</div>
                </div>
              </div>
              <div v-if="yueduSyncResult.errors && yueduSyncResult.errors.length" class="p-3 rounded bg-red-50 dark:bg-red-950 text-sm">
                <p class="font-medium text-red-700 dark:text-red-400 mb-1">{{ i18n.t('admin_yuedu_errors', { n: yueduSyncResult.errors.length }) }}</p>
                <div class="max-h-32 overflow-y-auto space-y-1 text-xs text-red-600 dark:text-red-300">
                  <p v-for="(e, i) in yueduSyncResult.errors" :key="i">{{ e.source }}: {{ e.error }}</p>
                </div>
              </div>
            </div>

            <p v-if="yueduError" class="text-sm text-red-600 mt-3">{{ yueduError }}</p>
            <div v-if="yueduResult" class="mt-3 p-3 rounded bg-green-50 dark:bg-green-950 text-sm">
              <p class="font-medium">{{ i18n.t('admin_yuedu_imported_count', { imported: yueduResult.imported, total: yueduResult.total }) }}</p>
              <p class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_skipped_count', { skipped: yueduResult.skipped }) }}</p>
              <p v-if="yueduResult.updated" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_updated_count', { updated: yueduResult.updated }) }}</p>
            </div>

            <details class="mt-3">
              <summary class="text-xs text-muted dark:text-gray-400 cursor-pointer hover:text-ink">{{ i18n.t('admin_yuedu_advanced') }}</summary>
              <div class="mt-3 space-y-3">
                <textarea v-model="yueduJsonText" :placeholder="i18n.t('admin_yuedu_advanced_json_placeholder')" rows="3" class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y" />
                <div class="flex gap-3">
                  <button @click="yueduPreviewAction" :disabled="yueduPreviewing" class="px-3 py-1.5 rounded border border-border dark:border-gray-700 text-xs hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
                    {{ yueduPreviewing ? '...' : i18n.t('admin_yuedu_btn_preview') }}
                  </button>
                  <button @click="yueduImport" :disabled="yueduImporting" class="px-3 py-1.5 rounded border border-accent text-accent text-xs hover:bg-accent/10 disabled:opacity-50">
                    {{ yueduImporting ? '...' : i18n.t('admin_yuedu_btn_import_only') }}
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
          <input
            v-model="localPath"
            :placeholder="i18n.t('admin_local_path_placeholder')"
            class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
          />
          <p v-if="localError" class="text-sm text-red-600 mt-2">{{ localError }}</p>
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
                <span class="block text-xs text-muted dark:text-gray-400 truncate">{{ book.author }} · {{ book.chapter_count }} chapters · {{ book.format }}</span>
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
            </p>
          </div>
        </div>

        <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 max-w-3xl">
          <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_manual_upload') }}</h2>
          <p class="text-xs text-muted dark:text-gray-400 mb-3" v-html="i18n.t('admin_manual_hint')"></p>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <input v-model="manualTitle" :placeholder="i18n.t('admin_manual_title_placeholder')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
            <input v-model="manualAuthor" :placeholder="i18n.t('admin_manual_author_placeholder')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <select v-model="manualStatus" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800">
              <option value="ongoing">{{ i18n.t('admin_manual_status_ongoing') }}</option>
              <option value="completed">{{ i18n.t('admin_manual_status_completed') }}</option>
            </select>
            <input v-model="manualTags" :placeholder="i18n.t('admin_manual_tags_placeholder')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
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
            <label class="inline-flex px-3 py-2 rounded border border-border dark:border-gray-700 text-sm cursor-pointer hover:bg-accent/5">
              {{ i18n.t('admin_manual_pick_file') }}
              <input type="file" accept=".txt,.md,text/plain,text/markdown" class="hidden" @change="onManualFile" />
            </label>
            <span class="text-xs text-muted dark:text-gray-400" v-html="i18n.t('admin_manual_chapter_hint')"></span>
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
        <p v-if="approvalError" class="text-sm text-red-600 mb-3">{{ approvalError }}</p>
        <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
          <div v-for="a in approvals" :key="a.id" class="px-4 py-3 flex items-center justify-between flex-wrap gap-2">
            <div>
              <span class="text-xs px-1.5 py-0.5 rounded-full mr-2" :class="a.action === 'create' ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' : 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300'">{{ a.action }}</span>
              <template v-if="a.action === 'create' && a.source_data">
                <span class="text-sm font-medium">{{ a.source_data.name }}</span>
                <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ a.source_data.id }} ({{ a.source_data.plugin_name }})</span>
              </template>
              <template v-else-if="a.source_id">
                <span class="text-sm font-medium">{{ i18n.t('admin_delete_source', { id: a.source_id }) }}</span>
              </template>
              <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ i18n.t('admin_by_user', { id: a.user_id?.slice(0, 8) }) }}...</span>
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
