import { defineStore } from "pinia"
import { computed, ref } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "./i18n"
import { useAdminSourcesStore } from "./adminSources"

/** State behind the "add" tab: local markdown import + manual book upload. */
export const useAdminImportStore = defineStore("adminImport", () => {
  const i18n = useI18nStore()

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

  const allLocalSelected = computed(() =>
    localScan.value.length > 0 && localSelected.value.length === localScan.value.length
  )

  async function importLocal() {
    const sources = useAdminSourcesStore()
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
      await sources.loadSources()
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
    const sources = useAdminSourcesStore()
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
      await sources.loadSources()
    } catch (e) {
      localError.value = e instanceof Error ? e.message : i18n.t('admin_local_import_failed')
    } finally {
      localImporting.value = false
    }
  }

  async function importLocalAll() {
    const sources = useAdminSourcesStore()
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
      await sources.loadSources()
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

  return {
    localPath,
    localImporting,
    localResult,
    localError,
    localScan,
    localScanRoot,
    localSelected,
    localScanning,
    localDirectResult,
    localRoots,
    localClass,
    localBrowserOpen,
    localBrowserPath,
    localBrowserName,
    localBrowserParent,
    localBrowserDirs,
    localBrowsing,
    manualTitle,
    manualAuthor,
    manualStatus,
    manualDescription,
    manualTags,
    manualChaptersText,
    manualImporting,
    manualAnalyzing,
    manualAnalyzeResult,
    manualResult,
    manualError,
    manualClass,
    allLocalSelected,
    importLocal,
    loadLocalRoots,
    openLocalBrowser,
    browseLocalDirectory,
    chooseLocalDirectory,
    scanLocal,
    toggleLocalBook,
    toggleAllLocalBooks,
    invertLocalBooks,
    clearLocalBooks,
    importLocalSelected,
    importLocalAll,
    directLocalSelected,
    onManualFile,
    analyzeManualText,
    submitManualBook,
  }
})
