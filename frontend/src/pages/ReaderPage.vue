<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useBooksStore, type Chapter, type ChapterContent } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import { api } from "../api/client"
import { useChunkedList } from "../utils/useChunkedList"
import AIChat from "../components/AIChat.vue"
import AISelectionToolbar from "../components/AISelectionToolbar.vue"
import ImageViewer from "../components/ImageViewer.vue"
import ReaderAppearancePanel from "../components/ReaderAppearancePanel.vue"
import {
  originalText,
  readReaderAppearance,
  readerAppearanceKey,
  readerPalettes,
  readerTextConverter,
  type ReaderScript,
} from "../utils/readerAppearance"
import {
  DEFAULT_TAP_ACTIONS,
  TAP_ACTIONS,
  TAP_REGION_KEYS,
  normalizeTapActions,
  type ReaderTapAction,
  type ReaderTapActions,
  type ReaderTapRegion,
} from "../utils/readerTapAreas"

type MobilePageMode = "cover" | "slide" | "simulation" | "scroll" | "none"

const mobilePageModes: MobilePageMode[] = ["cover", "slide", "simulation", "scroll", "none"]

function savedPageMode(): MobilePageMode {
  const value = localStorage.getItem("novelhub_page_mode") as MobilePageMode | null
  return value && mobilePageModes.includes(value) ? value : "cover"
}

const route = useRoute()
const router = useRouter()
const store = useBooksStore()
const auth = useAuthStore()
const i18n = useI18nStore()

const chapter = ref<ChapterContent | null>(null)
const chapters = ref<Chapter[]>([])
// The TOC drawer mounts chapters in chunks of 200 instead of all at once.
const { visible: visibleTocChapters, hasMore: hasMoreTocChapters, sentinel: tocSentinel } =
  useChunkedList(chapters)
const loading = ref(true)
const error = ref("")
const appearance = ref(readReaderAppearance())
const showAppearance = ref(false)
const isDark = computed(() => auth.isDark)
const converter = shallowRef(originalText)
const activeScript = ref<ReaderScript>("original")
const scriptLoading = ref(false)
const scriptFailed = ref(false)
const scriptAttempt = ref(0)
const readingLanguage = computed(() => {
  if (activeScript.value === "simplified") return "zh-Hans"
  if (activeScript.value === "traditional") return "zh-Hant"
  return undefined
})
const showToc = ref(false)
const showAI = ref(false)
// Text container the selected-passage AI toolbar attaches to (desktop article
// or the mobile scroll article; the paged mobile layout is column-based).
const desktopTextRef = ref<HTMLElement | null>(null)
const mobileTextRef = ref<HTMLElement | null>(null)
const aiChatRef = ref<InstanceType<typeof AIChat> | null>(null)
const aiSelectionContainer = computed(() =>
  isMobileLayout.value ? mobileTextRef.value : desktopTextRef.value,
)

function askAIAbout(text: string) {
  if (!text) return
  showAI.value = true
  nextTick(() => aiChatRef.value?.ask(text))
}

function openAISettings() {
  router.push({ path: "/admin", query: { tab: "ai" } })
}
const chapterSyncing = ref(false)
const alternates = ref<any[]>([])
const showSourceMenu = ref(false)

const showContentImages = computed(() => auth.user?.settings?.show_content_images !== false)
const viewedImage = ref<{ src: string; alt: string } | null>(null)

const isMobileLayout = ref(false)
const menuVisible = ref(false)
const currentPage = ref(0)
const pageCount = ref(1)
const pageMode = ref<MobilePageMode>(savedPageMode())
const mobileScrollProgress = ref(0)
const pendingPage = ref<number | "last" | null>(null)
const pendingRestorePercent = ref<number | null>(null)
const nextContentOffset = ref<number | null>(null)
const contentChunkLoading = ref(false)
const contentTotalLength = ref(0)
const contentChunkError = ref("")
// A single chapter can hold hundreds of thousands of characters (some books
// are one chapter), so the body arrives in chunks.  Loading the rest used to
// depend on one invisible trigger -- scrolling to within 900 px of the bottom
// -- and a reload dropped everything that had been appended, which left the
// reader showing only the first chunk with no way forward.  Now the first
// chunk paints, the remainder is fetched in the background, and there is an
// explicit control whenever anything is still missing.
const CHUNK_LIMIT = 200_000
const AUTO_LOAD_MAX_CHUNKS = 8
// Identifies the stored body the loaded chunks belong to (content digest, or
// the length for chapters written before digests existed).
const contentVersion = ref("")
let contentAutoLoadTimer: ReturnType<typeof setTimeout> | undefined
interface BookmarkItem {
  id: string
  book_id: string
  chapter_id: string
  position: number
  note?: string | null
  chapter_title?: string | null
  chapter_number?: number | null
}

const bookmarks = ref<BookmarkItem[]>([])
const bookmarkBusy = ref(false)
const bookmarkToast = ref("")
const tocTab = ref<"toc" | "bookmarks">("toc")
const pendingBookmarkPosition = ref<number | null>(null)
const desktopProgress = ref(0)
let bookmarkToastTimer: ReturnType<typeof setTimeout>

const readLocation = ref<{ book_id: string; chapter_id: string } | null>(null)
const pageContent = ref<HTMLElement | null>(null)
const pageViewport = ref<HTMLElement | null>(null)
const scrollViewport = ref<HTMLElement | null>(null)
const showPageModeMenu = ref(false)
const showTapAreaMenu = ref(false)
const selectedTapRegion = ref<ReaderTapRegion>("mc")
const tapAreaDirty = ref(false)
const tapAreaSaving = ref(false)
const tapAreaSaved = ref(false)
const tapAreaError = ref("")

let mediaQuery: MediaQueryList | null = null
let mediaListener: EventListener | null = null
let scrollTimer: ReturnType<typeof setTimeout>
let resizeTimer: ReturnType<typeof setTimeout>
let progressTimer: ReturnType<typeof setTimeout>
let chapterLoadSeq = 0
let touchStartX = 0
let touchStartY = 0
let touchStartTime = 0
let suppressClick = false

const cnFonts = computed(() => [
  { value: "default", label: i18n.t('reader_font_system') },
  { value: "song", label: i18n.t('reader_font_song') },
  { value: "kai", label: i18n.t('reader_font_kai') },
  { value: "hei", label: i18n.t('reader_font_hei') },
  { value: "fang", label: i18n.t('reader_font_fang') },
])
const enFonts = computed(() => [
  { value: "default", label: i18n.t('reader_font_system') },
  { value: "serif", label: i18n.t('reader_font_serif') },
  { value: "sans", label: i18n.t('reader_font_sans') },
  { value: "mono", label: i18n.t('reader_font_mono') },
])
const pageModeOptions = computed<{ value: MobilePageMode; label: string }[]>(() => [
  { value: "cover", label: i18n.t("reader_page_mode_cover") },
  { value: "slide", label: i18n.t("reader_page_mode_slide") },
  { value: "simulation", label: i18n.t("reader_page_mode_simulation") },
  { value: "scroll", label: i18n.t("reader_page_mode_scroll") },
  { value: "none", label: i18n.t("reader_page_mode_none") },
])

function savedTapActions(): ReaderTapActions {
  const raw = localStorage.getItem("novelhub_tap_actions")
  if (raw) {
    try {
      return normalizeTapActions(JSON.parse(raw))
    } catch {
      /* fall back to defaults */
    }
  }
  return { ...DEFAULT_TAP_ACTIONS }
}

const tapActions = ref<ReaderTapActions>(
  auth.user?.settings?.tap_actions
    ? normalizeTapActions(auth.user.settings.tap_actions)
    : savedTapActions(),
)

const tapActionOptions = computed(() =>
  TAP_ACTIONS.map((action) => ({
    value: action,
    label: i18n.t("reader_tap_action_" + action),
  })),
)

function tapActionLabel(action: ReaderTapAction): string {
  return i18n.t("reader_tap_action_" + action)
}

const cnFontStack: Record<string, string> = {
  default: "",
  song: '"SimSun", "Noto Serif CJK SC", "Source Han Serif SC", serif',
  kai: '"KaiTi", "Noto Serif CJK SC", "STKaiti", serif',
  hei: '"SimHei", "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", sans-serif',
  fang: '"FangSong", "Noto Serif CJK SC", "STFangsong", serif',
}
const enFontStack: Record<string, string> = {
  default: "",
  serif: 'Georgia, "Times New Roman", "Noto Serif", serif',
  sans: 'system-ui, -apple-system, "Segoe UI", "Noto Sans", sans-serif',
  mono: '"Courier New", "Consolas", "JetBrains Mono", monospace',
}

const bookId = computed(() => route.params.bookId as string)
const chapterId = computed(() => route.params.chapterId as string)

const currentIndex = computed(() => chapters.value.findIndex((c) => c.id === chapterId.value))
const prevChapter = computed(() =>
  currentIndex.value > 0 ? chapters.value[currentIndex.value - 1] : null,
)
const nextChapter = computed(() =>
  currentIndex.value < chapters.value.length - 1 ? chapters.value[currentIndex.value + 1] : null,
)

const readerFontStyle = computed(() => {
  const cn = cnFontStack[appearance.value.cnFont] || ""
  const en = enFontStack[appearance.value.enFont] || ""
  const stack = [en, cn].filter(Boolean).join(", ")
  return {
    fontSize: appearance.value.fontSize + "px",
    ...(stack ? { fontFamily: stack } : {}),
  }
})

const readerCanvasStyle = computed(() => {
  const current = appearance.value
  const palette = current.palette === "custom" ? current
    : readerPalettes[current.palette === "system" ? (isDark.value ? "night" : "day") : current.palette]
  return {
    "--reader-background": palette.background,
    "--reader-foreground": palette.foreground,
    "--reader-paragraph-indent": `${current.indent}em`,
    "--reader-line-height": current.lineHeight,
  }
})

function displayText(text: string): string {
  return converter.value(text)
}

function chapterLabel(item: Pick<Chapter, "title" | "chapter_number">): string {
  return item.title
    ? displayText(item.title)
    : i18n.t('reader_chapter_fallback', { n: item.chapter_number })
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
}

// Only real image URLs are allowed through; anything scheme-relative or
// javascript:-like is dropped before it can reach v-html.
function safeImageUrl(url: string): string | null {
  if (/^(https?:\/\/|\/(?![/\\]))/i.test(url)) return url
  return null
}

function decodeEntities(text: string): string {
  return text
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
}

/**
 * Chapter content -> reader HTML.
 *
 * Lift out validated images first. Convert and escape the remaining text
 * before inserting rendered image elements with their original URLs.
 */
function renderChapterHtml(content: string, title: string | null, openImageLabel: string): string {
  const images: string[] = []
  function captureImage(url: string, alt: string): string {
    const safe = safeImageUrl(decodeEntities(url))
    if (!safe) return ""
    const label = alt ? openImageLabel + " · " + decodeEntities(alt) : openImageLabel
    images.push(
      '<button type="button" class="chapter-image-trigger" aria-label="' + escapeHtml(label) + '">' +
      '<img src="' + escapeHtml(safe) + '" alt="' + escapeHtml(decodeEntities(alt)) + '" loading="lazy" decoding="async">' +
      '</button>',
    )
    return "\u0000img" + (images.length - 1) + "\u0000"
  }
  const source = String(content || "").replace(/\r\n?/g, "\n")
  const storedTitle = /^#[ \t]*([^\n]+)\n/.exec(source)
  const body = storedTitle && storedTitle[1].trim() === title?.trim()
    ? source.slice(storedTitle[0].length)
    : source
  const text = body
    // Some sources store the chapter markup already escaped ("&lt;img ...&gt;"),
    // and the reader then showed the tag itself as text.  Decode just those
    // image tags first; everything else stays escaped.
    .replace(/&lt;img\b[\s\S]*?&gt;/gi, (escaped: string) => decodeEntities(escaped))
    .replace(/<img\b[^>]*>/gi, (tag: string) => {
      const src = /\ssrc\s*=\s*(?:"([^"]+)"|'([^']+)')/i.exec(tag)
      const alt = /\salt\s*=\s*(?:"([^"]*)"|'([^']*)')/i.exec(tag)
      return captureImage(src?.[1] ?? src?.[2] ?? "", alt?.[1] ?? alt?.[2] ?? "")
    })
    .replace(
      /!\[([^\]]*)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g,
      (_match, alt: string, url: string) => captureImage(url, alt),
    )
  return text.split("\n").filter((paragraph) => paragraph.trim()).map((paragraph) => {
    const parts = paragraph.replace(/^[\t \u00a0\u3000]+/, "").split(/\u0000img(\d+)\u0000/g)
    const hasText = parts.some((part, index) => index % 2 === 0 && part.trim())
    const html = parts.map((part, index) => index % 2 === 0
      ? escapeHtml(displayText(part))
      : images[Number(part)] ?? "").join("")
    return hasText ? `<p>${html}</p>` : `<p class="reader-illustration">${html}</p>`
  }).join("")
}

const chapterBodyHtml = computed(() =>
  chapter.value ? renderChapterHtml(chapter.value.content, chapter.value.title, i18n.t('image_viewer_open')) : "",
)

const pageProgress = computed(() => {
  if (pageCount.value <= 1) return 100
  return Math.round((currentPage.value / (pageCount.value - 1)) * 100)
})
const mobileProgress = computed(() => pageMode.value === "scroll" ? mobileScrollProgress.value : pageProgress.value)
const mobileProgressLabel = computed(() => pageMode.value === "scroll"
  ? `${mobileScrollProgress.value}%`
  : `${currentPage.value + 1} / ${pageCount.value}`)

function savePosition(position: number) {
  if (!auth.user || !chapter.value || !readLocation.value) return
  api.put("/progress", {
    user_id: auth.user.id,
    book_id: readLocation.value.book_id,
    chapter_id: readLocation.value.chapter_id,
    position,
  }).catch(() => {})
}

function desktopScrollPercent(): number {
  const max = document.documentElement.scrollHeight - window.innerHeight
  if (max <= 0) return 100
  return Math.round((window.scrollY / max) * 100)
}
function currentPosition(): number {
  return isMobileLayout.value ? mobileProgress.value : desktopScrollPercent()
}
function onScroll() {
  desktopProgress.value = desktopScrollPercent()
  if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 900) {
    scheduleContentAutoLoad()
  }
  clearTimeout(scrollTimer)
  scrollTimer = setTimeout(() => {
    if (!auth.user || isMobileLayout.value) return
    savePosition(desktopScrollPercent())
  }, 2000)
}

function applyPageTransform() {
  const el = pageContent.value
  if (!el || pageMode.value === "scroll") return
  const styles = getComputedStyle(el)
  const columnWidth = parseFloat(styles.columnWidth) || el.clientWidth
  const gap = parseFloat(styles.columnGap) || 32
  el.style.transform = `translateX(${-(currentPage.value * (columnWidth + gap))}px)`
}

async function measurePages() {
  await nextTick()
  const el = pageContent.value
  if (!el || !isMobileLayout.value || pageMode.value === "scroll") return
  const styles = getComputedStyle(el)
  const columnWidth = parseFloat(styles.columnWidth) || el.clientWidth
  const gap = parseFloat(styles.columnGap) || 32
  const count = Math.max(1, Math.round((el.scrollWidth + gap) / (columnWidth + gap)))
  pageCount.value = count
  if (pendingPage.value !== null) {
    currentPage.value = pendingPage.value === "last"
      ? count - 1
      : Math.min(pendingPage.value, count - 1)
    pendingPage.value = null
  } else {
    currentPage.value = Math.min(currentPage.value, count - 1)
  }
  if (currentPage.value < 0) currentPage.value = 0
  applyPageTransform()
}

async function refreshMobileLayout() {
  await nextTick()
  if (!isMobileLayout.value) return
  if (pageMode.value === "scroll") {
    const el = scrollViewport.value
    if (!el) return
    if (pendingPage.value === "last") el.scrollTop = el.scrollHeight
    else if (typeof pendingPage.value === "number" && pendingPage.value > 0) {
      el.scrollTop = (pendingPage.value / 100) * Math.max(0, el.scrollHeight - el.clientHeight)
    }
    pendingPage.value = null
    updateMobileScrollProgress()
    return
  }
  await measurePages()
}

function setPageMode(mode: MobilePageMode) {
  if (pageMode.value === mode) {
    showPageModeMenu.value = false
    return
  }
  pageMode.value = mode
  localStorage.setItem("novelhub_page_mode", mode)
  currentPage.value = 0
  mobileScrollProgress.value = 0
  pendingPage.value = 0
  showPageModeMenu.value = false
  nextTick(refreshMobileLayout)
}

function updateMobileScrollProgress() {
  const el = scrollViewport.value
  if (!el || pageMode.value !== "scroll") return
  const max = Math.max(0, el.scrollHeight - el.clientHeight)
  mobileScrollProgress.value = max === 0 ? 100 : Math.round((el.scrollTop / max) * 100)
}

function onMobileScroll() {
  updateMobileScrollProgress()
  const el = scrollViewport.value
  if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - 900) {
    scheduleContentAutoLoad()
  }
  clearTimeout(progressTimer)
  progressTimer = setTimeout(() => savePosition(mobileScrollProgress.value), 800)
}

function seekMobileProgress(event: Event) {
  const value = Number((event.target as HTMLInputElement).value)
  if (pageMode.value === "scroll") {
    const el = scrollViewport.value
    if (!el) return
    el.scrollTop = (value / 100) * Math.max(0, el.scrollHeight - el.clientHeight)
    updateMobileScrollProgress()
  } else {
    currentPage.value = pageCount.value <= 1
      ? 0
      : Math.round((value / 100) * (pageCount.value - 1))
    applyPageTransform()
  }
  queuePageProgress()
}

function queuePageProgress() {
  clearTimeout(progressTimer)
  progressTimer = setTimeout(() => {
    if (isMobileLayout.value) savePosition(mobileProgress.value)
  }, 800)
}

function flushPageProgress() {
  clearTimeout(progressTimer)
  if (!auth.user || !chapter.value) return
  savePosition(currentPosition())
}

// Keepalive flush used on page hide so progress survives tab/browser close.
function flushPageProgressKeepalive() {
  clearTimeout(progressTimer)
  if (!auth.user || !chapter.value || !readLocation.value) return
  const payload = JSON.stringify({
    user_id: auth.user.id,
    book_id: readLocation.value.book_id,
    chapter_id: readLocation.value.chapter_id,
    position: Math.round(currentPosition()),
  })
  try {
    fetch("/api/progress", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (localStorage.getItem("novelhub_token") || ""),
      },
      body: payload,
      keepalive: true,
    }).catch(() => {})
  } catch { /* ignore */ }
}

// ---------------- bookmarks ----------------

async function loadBookmarks() {
  if (!auth.user) return
  try {
    bookmarks.value = await api.get<BookmarkItem[]>("/bookmarks?book_id=" + encodeURIComponent(bookId.value))
  } catch {
    bookmarks.value = []
  }
}

function currentBookmark(): BookmarkItem | null {
  return bookmarks.value.find((b) => b.chapter_id === chapterId.value) || null
}

function showBookmarkToast(msg: string) {
  bookmarkToast.value = msg
  clearTimeout(bookmarkToastTimer)
  bookmarkToastTimer = setTimeout(() => { bookmarkToast.value = "" }, 1800)
}

async function toggleBookmark() {
  if (!auth.user || !chapter.value || bookmarkBusy.value) return
  const existing = currentBookmark()
  bookmarkBusy.value = true
  try {
    if (existing) {
      await api.delete("/bookmarks/" + existing.id)
      bookmarks.value = bookmarks.value.filter((b) => b.id !== existing.id)
      showBookmarkToast(i18n.t("reader_bookmark_removed"))
    } else {
      const created = await api.post<BookmarkItem>("/bookmarks", {
        book_id: bookId.value,
        chapter_id: chapterId.value,
        position: Math.round(currentPosition()),
      })
      bookmarks.value = [created, ...bookmarks.value.filter((b) => b.id !== created.id)]
      showBookmarkToast(i18n.t("reader_bookmark_added"))
    }
  } catch { /* non-critical */ } finally {
    bookmarkBusy.value = false
  }
}

async function removeBookmark(id: string) {
  try {
    await api.delete("/bookmarks/" + id)
    bookmarks.value = bookmarks.value.filter((b) => b.id !== id)
  } catch { /* non-critical */ }
}

function jumpToBookmark(bm: BookmarkItem) {
  showToc.value = false
  closeMenu()
  const target = Math.min(100, Math.max(0, bm.position))
  if (bm.chapter_id === chapterId.value) {
    // Same chapter: restore the position directly.
    pendingRestorePercent.value = target
    applyRestoredPosition()
    return
  }
  pendingBookmarkPosition.value = target
  openChapter(bm.chapter_id, 0)
}
async function restoreChapterProgress() {
  pendingRestorePercent.value = null
  if (!auth.user || !chapter.value) return
  try {
    const res = await api.get<any>("/progress/" + bookId.value)
    if (
      res &&
      res.chapter_id === chapter.value.id &&
      typeof res.position === "number"
    ) {
      pendingRestorePercent.value = Math.min(100, Math.max(0, res.position))
    }
  } catch {
    /* no saved progress */
  }
}
function applyRestoredPosition() {
  const percent = pendingRestorePercent.value
  if (percent === null || !chapter.value) return
  if (isMobileLayout.value) {
    if (pageMode.value === "scroll") {
      const el = scrollViewport.value
      if (el) {
        el.scrollTop = (percent / 100) * Math.max(0, el.scrollHeight - el.clientHeight)
        updateMobileScrollProgress()
        pendingRestorePercent.value = null
      }
    } else if (pageCount.value > 1) {
      currentPage.value = Math.min(
        Math.round((percent / 100) * (pageCount.value - 1)),
        pageCount.value - 1,
      )
      applyPageTransform()
      pendingRestorePercent.value = null
    }
  } else {
    const max = document.documentElement.scrollHeight - window.innerHeight
    window.scrollTo(0, max > 0 ? (percent / 100) * max : 0)
    pendingRestorePercent.value = null
  }
}
function onContentLoad() {
  if (pendingRestorePercent.value === null) return
  applyRestoredPosition()
}
async function onMobileContentLoad() {
  await refreshMobileLayout()
  if (pendingRestorePercent.value !== null) applyRestoredPosition()
}
function onMobileScrollContentLoad() {
  updateMobileScrollProgress()
  if (pendingRestorePercent.value !== null) applyRestoredPosition()
}

function openChapter(id: string, page: number | "last" = 0, targetBookId = bookId.value) {
  closeMenu()
  showToc.value = false
  showAI.value = false
  pendingPage.value = page
  if (id === chapterId.value && targetBookId === bookId.value) {
    if (isMobileLayout.value) nextTick(refreshMobileLayout)
    return
  }
  router.replace("/books/" + targetBookId + "/chapters/" + id)
}

function nextPageOrChapter() {
  if (pageMode.value === "scroll") {
    const el = scrollViewport.value
    if (el && el.scrollTop < el.scrollHeight - el.clientHeight - 4) {
      el.scrollBy({ top: Math.max(120, el.clientHeight - 48), behavior: "smooth" })
      return
    }
    if (nextContentOffset.value !== null) {
      void loadNextContentChunk()
      return
    }
    if (nextChapter.value) openChapter(nextChapter.value.id, 0)
    return
  }
  if (currentPage.value < pageCount.value - 1) {
    currentPage.value += 1
    applyPageTransform()
    queuePageProgress()
    return
  }
  if (nextContentOffset.value !== null) {
    const previousLastPage = pageCount.value - 1
    void loadNextContentChunk().then((loaded) => {
      if (loaded) {
        currentPage.value = Math.min(previousLastPage + 1, pageCount.value - 1)
        applyPageTransform()
        queuePageProgress()
      }
    })
    return
  }
  if (nextChapter.value) openChapter(nextChapter.value.id, 0)
}

function prevPageOrChapter() {
  if (pageMode.value === "scroll") {
    const el = scrollViewport.value
    if (el && el.scrollTop > 4) {
      el.scrollBy({ top: -Math.max(120, el.clientHeight - 48), behavior: "smooth" })
      return
    }
    if (prevChapter.value) openChapter(prevChapter.value.id, "last")
    return
  }
  if (currentPage.value > 0) {
    currentPage.value -= 1
    applyPageTransform()
    queuePageProgress()
    return
  }
  if (prevChapter.value) openChapter(prevChapter.value.id, "last")
}

function handleTap(event: MouseEvent) {
  if (menuVisible.value) {
    closeMenu()
    return
  }
  if (suppressClick) {
    suppressClick = false
    return
  }
  if (openChapterImage(event)) return
  const action = tapActionFor(event)
  if (action === "prev_page") prevPageOrChapter()
  else if (action === "next_page") nextPageOrChapter()
  else if (action === "prev_chapter" && prevChapter.value) {
    openChapter(prevChapter.value.id, pageMode.value === "scroll" ? "last" : 0)
  } else if (action === "next_chapter" && nextChapter.value) {
    openChapter(nextChapter.value.id)
  } else if (action === "menu") {
    menuVisible.value = true
  }
}

function openChapterImage(event: MouseEvent): boolean {
  const target = event.target
  if (!showContentImages.value || !(target instanceof Element)) return false
  const image = target.closest(".chapter-image-trigger")?.querySelector("img")
  if (!image) return false
  viewedImage.value = { src: image.currentSrc || image.src, alt: image.alt }
  return true
}

function selectTapRegion(region: ReaderTapRegion) {
  selectedTapRegion.value = region
  tapAreaSaved.value = false
}

function setTapAction(action: ReaderTapAction) {
  tapActions.value[selectedTapRegion.value] = action
  tapAreaDirty.value = true
  tapAreaSaved.value = false
  tapAreaError.value = ""
  localStorage.setItem("novelhub_tap_actions", JSON.stringify(tapActions.value))
}

function resetTapActions() {
  tapActions.value = { ...DEFAULT_TAP_ACTIONS }
  tapAreaDirty.value = true
  tapAreaSaved.value = false
  tapAreaError.value = ""
  localStorage.setItem("novelhub_tap_actions", JSON.stringify(tapActions.value))
  if (auth.user) saveTapActions()
}

async function saveTapActions() {
  if (!auth.user) return
  tapAreaSaving.value = true
  tapAreaError.value = ""
  try {
    const actions = normalizeTapActions(tapActions.value)
    tapActions.value = actions
    localStorage.setItem("novelhub_tap_actions", JSON.stringify(actions))
    const res = await api.put<any>("/auth/me/settings", { tap_actions: actions })
    auth.user = res
    tapAreaDirty.value = false
    tapAreaSaved.value = true
  } catch (e) {
    tapAreaError.value = e instanceof Error ? e.message : i18n.t("reader_tap_area_save_failed")
  } finally {
    tapAreaSaving.value = false
  }
}

function tapActionFor(event: MouseEvent): ReaderTapAction {
  const width = window.innerWidth || 1
  const height = window.innerHeight || 1
  const col = Math.min(2, Math.max(0, Math.floor((event.clientX / width) * 3)))
  const row = Math.min(2, Math.max(0, Math.floor((event.clientY / height) * 3)))
  const region = TAP_REGION_KEYS[row * 3 + col]
  return tapActions.value[region]
}

function onTouchStart(event: TouchEvent) {
  if (menuVisible.value) return
  if (event.touches.length !== 1) {
    touchStartTime = 0
    return
  }
  const touch = event.changedTouches[0]
  touchStartX = touch.clientX
  touchStartY = touch.clientY
  touchStartTime = Date.now()
  suppressClick = false
}

function onTouchEnd(event: TouchEvent) {
  if (menuVisible.value || pageMode.value === "scroll" || !touchStartTime || event.touches.length) return
  const touch = event.changedTouches[0]
  const dx = touch.clientX - touchStartX
  const dy = touch.clientY - touchStartY
  const dt = Date.now() - touchStartTime
  if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy) * 1.5 && dt < 700) {
    suppressClick = true
    if (dx < 0) nextPageOrChapter()
    else prevPageOrChapter()
    setTimeout(() => {
      suppressClick = false
    }, 350)
  }
}

function closeMenu() {
  menuVisible.value = false
  showAppearance.value = false
  showSourceMenu.value = false
  showPageModeMenu.value = false
  showTapAreaMenu.value = false
}

function goBackToBook() {
  const destination = { name: "book-detail", params: { id: bookId.value } }
  const back = (window.history.state as { back?: string | null } | null)?.back
  if (back && router.resolve(back).path === router.resolve(destination).path) {
    router.back()
  } else {
    router.replace(destination)
  }
}

function updateMobileLayout() {
  const next = window.matchMedia("(max-width: 900px) and (pointer: coarse), (max-width: 700px)").matches
  if (next === isMobileLayout.value) return
  isMobileLayout.value = next
  document.documentElement.classList.toggle("reader-locked", next)
  if (next) {
    currentPage.value = 0
    nextTick(refreshMobileLayout)
  }
}

function onResize() {
  clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => {
    if (isMobileLayout.value) nextTick(refreshMobileLayout)
  }, 150)
}

async function loadNextContentChunk(): Promise<boolean> {
  const offset = nextContentOffset.value
  if (offset === null || contentChunkLoading.value || !chapter.value) return false
  contentChunkLoading.value = true
  contentChunkError.value = ""
  try {
    const chunk = await store.fetchChapterChunk(
      chapter.value.id,
      offset,
      CHUNK_LIMIT,
      contentVersion.value,
    )
    if (!chapter.value || chapter.value.id !== chunk.id) return false
    // Guard against a duplicate append when two callers raced.
    if (chunk.offset < (chapter.value.content?.length || 0)) {
      nextContentOffset.value = chunk.next_offset
      contentTotalLength.value = chunk.total_length
      return false
    }
    chapter.value = {
      ...chapter.value,
      content: chapter.value.content + chunk.content,
      hash: chunk.hash ?? chapter.value.hash,
    }
    nextContentOffset.value = chunk.next_offset
    contentTotalLength.value = chunk.total_length
    if (isMobileLayout.value) await refreshMobileLayout()
    return true
  } catch (e) {
    // Swallowing this silently is what made a long chapter look truncated with
    // no explanation; show it and offer a retry instead.
    contentChunkError.value = e instanceof Error ? e.message : i18n.t("reader_chunk_failed")
    return false
  } finally {
    contentChunkLoading.value = false
  }
}

const contentLoadedLength = computed(() => chapter.value?.content?.length || 0)
const contentHasMore = computed(() => nextContentOffset.value !== null)
const contentRemaining = computed(() =>
  Math.max(0, contentTotalLength.value - contentLoadedLength.value),
)

/**
 * Keep pulling the remaining chunks in the background until the chapter is
 * complete (or the bound is hit), so reading never runs into a cliff that the
 * user has to discover by scrolling.
 */
function scheduleContentAutoLoad() {
  clearTimeout(contentAutoLoadTimer)
  contentAutoLoadTimer = setTimeout(async () => {
    let loaded = 0
    while (
      nextContentOffset.value !== null &&
      !contentChunkError.value &&
      loaded < AUTO_LOAD_MAX_CHUNKS
    ) {
      const ok = await loadNextContentChunk()
      if (!ok) break
      loaded += 1
    }
  }, 400)
}

async function loadRemainingContent() {
  contentChunkError.value = ""
  let guard = 0
  while (nextContentOffset.value !== null && guard < 200) {
    const ok = await loadNextContentChunk()
    if (!ok) break
    guard += 1
  }
}

async function loadChapter(id: string) {
  const seq = ++chapterLoadSeq
  flushPageProgress()
  loading.value = true
  error.value = ""
  viewedImage.value = null
  currentPage.value = 0
  pageCount.value = 1
  mobileScrollProgress.value = 0
  pendingRestorePercent.value = null
  nextContentOffset.value = null
  contentTotalLength.value = 0
  contentChunkError.value = ""
  clearTimeout(contentAutoLoadTimer)
  try {
    // Length first (a few hundred bytes), so the reader knows from the start
    // whether this is a partial view and whether its cached chunks still match
    // the stored body.
    let version = ""
    try {
      const meta = await store.fetchChapterContentMeta(id)
      version = meta.hash || (meta.total_length ? `len${meta.total_length}` : "")
      contentTotalLength.value = meta.total_length
    } catch {
      // Backend without the meta endpoint: the digest is unknown until the
      // first chunk arrives, so that request must not be served from cache.
    }
    if (seq !== chapterLoadSeq) return
    const loaded = await store.fetchChapterChunk(id, 0, CHUNK_LIMIT, version, !version)
    if (seq !== chapterLoadSeq) return
    contentVersion.value = version
      || loaded.hash
      || (loaded.total_length ? `len${loaded.total_length}` : "")
    chapter.value = loaded
    nextContentOffset.value = loaded.next_offset
    contentTotalLength.value = loaded.total_length || contentTotalLength.value
    readLocation.value = { book_id: bookId.value, chapter_id: id }
    loading.value = false
    // Warm the next chapter while the reader lays out the current one.
    const next = chapters.value.find((item) => item.chapter_number > loaded.chapter_number)
    if (next) void store.prefetchChapter(next.id)
    const hasPendingPage = pendingPage.value !== null
    if (isMobileLayout.value) await refreshMobileLayout()
    await nextTick()
    if (seq !== chapterLoadSeq) return
    if (!hasPendingPage) {
      await restoreChapterProgress()
      if (seq !== chapterLoadSeq) return
      applyRestoredPosition()
    } else if (pendingBookmarkPosition.value !== null) {
      pendingRestorePercent.value = Math.min(100, Math.max(0, pendingBookmarkPosition.value))
      pendingBookmarkPosition.value = null
      if (seq !== chapterLoadSeq) return
      applyRestoredPosition()
    }
    // Only now start prefetching the rest of a long chapter.
    if (nextContentOffset.value !== null) scheduleContentAutoLoad()
  } catch (e) {
    if (seq !== chapterLoadSeq) return
    error.value = e instanceof Error ? e.message : i18n.t('reader_failed_load_chapter')
  } finally {
    if (seq === chapterLoadSeq) loading.value = false
  }
}

async function resyncChapter() {
  if (!chapter.value) return
  chapterSyncing.value = true
  try {
    await api.post('/chapters/' + chapter.value.id + '/sync')
    store.invalidateChapter(chapter.value.id)
    await loadChapter(chapter.value.id)
    alert(i18n.t('reader_chapter_synced'))
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('reader_chapter_sync_failed'))
  } finally {
    chapterSyncing.value = false
  }
}

async function loadAlternates() {
  try {
    const res = await api.get<any>("/books/" + bookId.value + "/sources")
    alternates.value = res.sources || []
  } catch { /* non-critical */ }
}

async function switchSource(alt: any) {
  showSourceMenu.value = false
  if (alt.is_current || !chapter.value) return
  try {
    const targetChapters = await store.fetchChapters(alt.id)
    const sameNumber = targetChapters.find(
      (c) => c.chapter_number === chapter.value?.chapter_number,
    )
    if (sameNumber) {
      openChapter(sameNumber.id, 0, alt.id)
    } else {
      closeMenu()
      router.replace("/books/" + alt.id)
    }
  } catch {
    closeMenu()
    router.replace("/books/" + alt.id)
  }
}

watch(appearance, (value) => {
  try {
    localStorage.setItem(readerAppearanceKey, JSON.stringify(value))
  } catch { /* private browsing can disable persistence */ }
}, { deep: true })

watch([() => appearance.value.script, scriptAttempt], async ([script], _previous, onCleanup) => {
  let current = true
  onCleanup(() => { current = false })
  scriptFailed.value = false
  scriptLoading.value = script !== "original"
  try {
    const nextConverter = await readerTextConverter(script)
    if (!current) return
    converter.value = nextConverter
    activeScript.value = script
  } catch {
    if (!current) return
    converter.value = originalText
    activeScript.value = "original"
    scriptFailed.value = true
  } finally {
    if (current) scriptLoading.value = false
  }
}, { immediate: true })

watch([
  () => appearance.value.fontSize,
  () => appearance.value.cnFont,
  () => appearance.value.enFont,
  () => appearance.value.indent,
  () => appearance.value.lineHeight,
  converter,
], async (_value, _previous, onCleanup) => {
  if (!chapter.value || loading.value) return
  let cancelled = false
  onCleanup(() => { cancelled = true })
  const id = chapter.value.id
  const hasAdvance = isMobileLayout.value
    ? (pageMode.value === "scroll" ? (scrollViewport.value?.scrollTop ?? 0) > 0 : currentPage.value > 0)
    : window.scrollY > 0
  const position = pendingRestorePercent.value ?? (hasAdvance ? currentPosition() : 0)
  await nextTick()
  if (cancelled || id !== chapter.value?.id || loading.value) return
  if (isMobileLayout.value) await refreshMobileLayout()
  if (cancelled || id !== chapter.value?.id || loading.value) return
  pendingRestorePercent.value = position
  applyRestoredPosition()
})

watch(
  () => auth.user?.settings?.tap_actions,
  (value) => {
    if (!tapAreaDirty.value) {
      tapActions.value = normalizeTapActions(value)
      localStorage.setItem("novelhub_tap_actions", JSON.stringify(tapActions.value))
    }
  },
)

watch(
  () => route.params.chapterId,
  async (newId) => {
    if (newId) await loadChapter(newId as string)
  },
)

// "打开即建": ask the backend to queue this book for background RAG indexing.
// The server owns every guard (setting off, comic, already indexed, daily cap),
// and the reader must never wait for this -- so the answer is ignored and a
// failure is silent.
async function requestAutoRagIndex(id: string | null | undefined) {
  if (!id) return
  try {
    await api.post(`/rag/auto/${encodeURIComponent(id)}`)
  } catch { /* best effort; chat still works in window mode */ }
}

watch(
  () => route.params.bookId,
  async (newId) => {
    if (!newId) return
    try {
      chapters.value = await store.fetchChapters(newId as string)
    } catch { /* non-fatal */ }
    // Same reasoning as onMounted: do not block the chapter on these.
    void loadAlternates()
    void loadBookmarks()
    void requestAutoRagIndex(newId as string)
    if (isMobileLayout.value) nextTick(refreshMobileLayout)
  },
)

onMounted(async () => {
  window.addEventListener("scroll", onScroll, { passive: true })
  window.addEventListener("resize", onResize, { passive: true })
  mediaQuery = window.matchMedia("(max-width: 900px) and (pointer: coarse), (max-width: 700px)")
  mediaListener = () => updateMobileLayout()
  mediaQuery.addEventListener?.("change", mediaListener)
  updateMobileLayout()
  try {
    chapters.value = await store.fetchChapters(bookId.value)
  } catch { /* non-fatal */ }
  // The chapter body is what the reader is waiting for. The "read on another
  // source" list and the bookmark list are secondary, so they must not sit in
  // front of it: awaiting them here used to delay the first paint of a chapter
  // by as long as /books/{id}/sources took.
  void loadAlternates()
  void loadBookmarks()
  void requestAutoRagIndex(bookId.value)
  await loadChapter(chapterId.value)
  window.addEventListener("pagehide", flushPageProgressKeepalive)
})

onUnmounted(() => {
  window.removeEventListener("scroll", onScroll)
  window.removeEventListener("resize", onResize)
  mediaQuery?.removeEventListener?.("change", mediaListener as EventListener)
  document.documentElement.classList.remove("reader-locked")
  clearTimeout(scrollTimer)
  clearTimeout(resizeTimer)
  clearTimeout(contentAutoLoadTimer)
  window.removeEventListener("pagehide", flushPageProgressKeepalive)
  flushPageProgress()
})
</script>

<template>
  <div class="reader-canvas min-h-screen" :style="readerCanvasStyle">
    <div
      v-if="bookmarkToast"
      class="bookmark-toast"
      :class="isDark ? 'bookmark-toast-dark' : 'bookmark-toast-light'"
    >{{ bookmarkToast }}</div>
    <div v-if="isMobileLayout" class="mobile-reader">
      <header v-if="!menuVisible || loading || error" class="reader-quickbar">
        <button type="button" @click="goBackToBook" class="reader-quick-action">
          <span aria-hidden="true">&larr;</span> {{ i18n.t('reader_book') }}
        </button>
        <button v-if="chapter && !loading && !error" type="button" @click="menuVisible = true" class="reader-quick-action">
          {{ i18n.t('reader_controls') }} <span aria-hidden="true">&ctdot;</span>
        </button>
      </header>
      <p
        v-if="loading"
        class="absolute inset-0 flex items-center justify-center text-sm opacity-70"
      >{{ i18n.t('reader_loading') }}</p>
      <p v-else-if="error" class="absolute inset-0 flex items-center justify-center px-8 text-sm text-red-500">{{ error }}</p>

      <template v-else-if="chapter">
        <div
          v-if="pageMode !== 'scroll'"
          class="page-surface"
          :class="'page-mode-' + pageMode"
          @touchstart.passive="onTouchStart"
          @touchend="onTouchEnd"
          @click="handleTap"
          @load.capture="onMobileContentLoad"
        >
          <div ref="pageViewport" class="page-viewport">
            <article ref="pageContent" class="page-columns" :style="readerFontStyle" :lang="readingLanguage">
              <div class="page-inner">
                <h1 class="page-title">
                  {{ chapterLabel(chapter) }}
                </h1>
                <div
                  class="page-body"
                  :class="{ 'hide-content-images': !showContentImages }"
                  v-html="chapterBodyHtml"
                />
              </div>
            </article>
          </div>
          <div class="page-meta">{{ currentPage + 1 }} / {{ pageCount }}</div>
          <button
            v-if="contentHasMore || contentChunkError"
            @click.stop="loadRemainingContent"
            :disabled="contentChunkLoading"
            class="page-chunk-hint"
          >{{ contentChunkError
              ? i18n.t('reader_chunk_retry')
              : i18n.t('reader_chunk_load_rest', { n: contentRemaining }) }}</button>
        </div>

        <div
          v-else
          ref="scrollViewport"
          class="scroll-page-surface"
          @scroll.passive="onMobileScroll"
          @click="handleTap"
          @load.capture="onMobileScrollContentLoad"
        >
          <article ref="mobileTextRef" class="scroll-page-content" :style="readerFontStyle" :lang="readingLanguage">
            <h1 class="page-title">
              {{ chapterLabel(chapter) }}
            </h1>
            <div
              class="page-body"
              :class="{ 'hide-content-images': !showContentImages }"
              v-html="chapterBodyHtml"
            />
            <div
              v-if="contentHasMore || contentChunkError"
              class="page-chunk-hint page-chunk-hint-scroll"
              @click.stop="loadRemainingContent"
            >{{ contentChunkError
                ? i18n.t('reader_chunk_retry')
                : i18n.t('reader_chunk_load_rest', { n: contentRemaining }) }}</div>
          </article>
          <div class="scroll-page-meta">{{ mobileScrollProgress }}%</div>
        </div>

        <div
          v-if="menuVisible"
          class="fixed inset-0 z-50 reader-menu-layer"
          :class="{ 'reader-menu-dark': isDark }"
          @click.self="closeMenu"
          @keydown.esc="closeMenu"
        >
          <div
            v-if="showAppearance || showSourceMenu || showPageModeMenu || showTapAreaMenu"
            class="fixed inset-0"
            @click="closeMenu"
          />
          <header class="reader-topbar" @click.stop>
            <button @click="goBackToBook" class="reader-top-action" :aria-label="i18n.t('reader_book')" :title="i18n.t('reader_book')">
              &larr;
            </button>
            <span class="reader-top-title" :lang="readingLanguage">
              {{ chapterLabel(chapter) }}
            </span>
            <button
              v-if="alternates.length > 1"
              @click="showSourceMenu = !showSourceMenu; showAppearance = false; showPageModeMenu = false; showTapAreaMenu = false"
              class="reader-top-action text-xs"
              :class="showSourceMenu ? 'text-accent' : ''"
              :title="i18n.t('reader_sources')"
            >{{ i18n.t('reader_sources_short') }}</button>
            <button
              @click="toggleBookmark"
              :disabled="bookmarkBusy"
              class="reader-top-action disabled:opacity-50"
              :class="currentBookmark() ? 'text-amber-400' : ''"
              :title="currentBookmark() ? i18n.t('reader_bookmark_remove') : i18n.t('reader_bookmark_add')"
            >{{ currentBookmark() ? '\u2605' : '\u2606' }}</button>
            <button
              v-if="auth.isAdmin"
              @click="resyncChapter"
              :disabled="chapterSyncing"
              class="reader-top-action disabled:opacity-50"
              :title="chapterSyncing ? i18n.t('reader_chapter_syncing') : i18n.t('reader_chapter_resync')"
            >&#8635;</button>
            <button @click="closeMenu" class="reader-top-action" :title="i18n.t('reader_close')">&times;</button>
          </header>

          <footer class="reader-bottombar" @click.stop>
            <div v-if="showSourceMenu" class="reader-bottom-panel">
              <button
                v-for="alt in alternates"
                :key="alt.id"
                @click="switchSource(alt)"
                class="reader-option-btn"
                :class="alt.is_current ? 'reader-option-active' : ''"
              >
                {{ alt.source_name || alt.source_id || i18n.t('reader_unknown') }}
                <span v-if="alt.is_current" class="ml-1 opacity-70">{{ i18n.t('reader_current') }}</span>
              </button>
            </div>

            <div v-if="showAppearance" class="reader-bottom-panel">
              <ReaderAppearancePanel
                v-model="appearance"
                :system-dark="isDark"
                :cn-fonts="cnFonts"
                :en-fonts="enFonts"
                :script-loading="scriptLoading"
                :script-failed="scriptFailed"
                @retry="scriptAttempt++"
              />
            </div>

            <div v-if="showPageModeMenu" class="reader-bottom-panel">
              <p class="reader-panel-label">{{ i18n.t('reader_page_mode') }}</p>
              <div class="reader-mode-grid">
                <button v-for="mode in pageModeOptions" :key="mode.value" @click="setPageMode(mode.value)" class="reader-option-btn" :class="pageMode === mode.value ? 'reader-option-active' : ''">{{ mode.label }}</button>
              </div>
            </div>

            <div v-if="showTapAreaMenu" class="reader-bottom-panel">
              <p class="reader-panel-label">{{ i18n.t('reader_tap_area') }}</p>
              <div class="tap-area-grid">
                <button
                  v-for="region in TAP_REGION_KEYS"
                  :key="region"
                  @click="selectTapRegion(region)"
                  class="tap-area-cell"
                  :class="[
                    selectedTapRegion === region ? 'tap-area-cell-active' : '',
                    tapActions[region] === 'menu' ? 'tap-area-cell-menu' : '',
                    tapActions[region] === 'none' ? 'tap-area-cell-none' : '',
                  ]"
                >
                  <span class="tap-area-cell-name">{{ region.toUpperCase() }}</span>
                  <span class="tap-area-cell-action">{{ tapActionLabel(tapActions[region]) }}</span>
                </button>
              </div>
              <p class="reader-panel-label">{{ i18n.t('reader_tap_area_action_for') }}</p>
              <div class="tap-action-grid">
                <button
                  v-for="option in tapActionOptions"
                  :key="option.value"
                  @click="setTapAction(option.value)"
                  class="reader-option-btn"
                  :class="tapActions[selectedTapRegion] === option.value ? 'reader-option-active' : ''"
                >{{ option.label }}</button>
              </div>
              <div class="tap-area-actions">
                <button @click="resetTapActions" class="reader-tap-reset-btn">{{ i18n.t('reader_tap_area_reset') }}</button>
                <button @click="saveTapActions" :disabled="tapAreaSaving || !tapAreaDirty || !auth.user" class="reader-tap-save-btn">{{ tapAreaSaving ? i18n.t('admin_saving') : i18n.t('reader_tap_area_save') }}</button>
              </div>
              <p v-if="tapAreaError" class="text-xs text-red-500 mt-2">{{ tapAreaError }}</p>
              <p v-else-if="!auth.user && tapAreaDirty" class="text-xs text-muted mt-2">{{ i18n.t('reader_tap_area_login_required') }}</p>
              <p v-else-if="tapAreaSaved" class="text-xs text-green-600 mt-2">{{ i18n.t('reader_tap_area_saved') }}</p>
            </div>

            <div class="reader-chapter-row">
              <button @click="prevChapter && openChapter(prevChapter.id, pageMode === 'scroll' ? 'last' : 0)" :disabled="!prevChapter" class="reader-chapter-btn">{{ i18n.t('reader_previous_chapter') }}</button>
              <div class="reader-progress-wrap">
                <input type="range" min="0" max="100" :value="mobileProgress" @input="seekMobileProgress" class="reader-progress" :title="i18n.t('reader_reading_progress')" />
                <span>{{ mobileProgressLabel }}</span>
              </div>
              <button @click="nextChapter && openChapter(nextChapter.id)" :disabled="!nextChapter" class="reader-chapter-btn">{{ i18n.t('reader_next_chapter') }}</button>
            </div>

            <nav class="reader-actions">
              <button @click="showToc = true; closeMenu()" class="reader-action-btn"><span class="reader-action-icon">&#9776;</span><span>{{ i18n.t('reader_toc') }}</span></button>
              <button @click="showAI = true; closeMenu()" class="reader-action-btn"><span class="reader-action-icon text-sm font-semibold">AI</span><span>{{ i18n.t('reader_ai') }}</span></button>
              <button @click="showAppearance = !showAppearance; showPageModeMenu = false; showSourceMenu = false; showTapAreaMenu = false" class="reader-action-btn" :aria-expanded="showAppearance" :class="showAppearance ? 'text-accent' : ''"><span class="reader-action-icon font-serif" aria-hidden="true">Aa</span><span>{{ i18n.t('reader_appearance') }}</span></button>
              <button @click="showPageModeMenu = !showPageModeMenu; showAppearance = false; showSourceMenu = false; showTapAreaMenu = false" class="reader-action-btn" :aria-expanded="showPageModeMenu" :class="showPageModeMenu ? 'text-accent' : ''"><span class="reader-action-icon" aria-hidden="true">&#8596;</span><span>{{ i18n.t('reader_page_mode') }}</span></button>
              <button @click="showTapAreaMenu = !showTapAreaMenu; showAppearance = false; showSourceMenu = false; showPageModeMenu = false" class="reader-action-btn" :aria-expanded="showTapAreaMenu" :class="showTapAreaMenu ? 'text-accent' : ''"><span class="reader-action-icon" aria-hidden="true">&#9638;</span><span>{{ i18n.t('reader_tap_area_short') }}</span></button>
            </nav>
          </footer>
        </div>
      </template>
    </div>

    <template v-else>
      <header
        class="sticky top-0 z-40 border-b h-12 flex items-center justify-between gap-2 px-4 backdrop-blur"
        :class="isDark ? 'bg-gray-900/95 border-gray-800 text-gray-100' : 'bg-surface/95 border-border text-ink'"
      >
        <div class="flex items-center gap-3 min-w-0">
          <button
            @click="goBackToBook"
            class="text-sm hover:opacity-70 transition-opacity"
          >&larr; {{ i18n.t('reader_book') }}</button>
          <button
            @click="showToc = !showToc"
            class="text-sm hover:opacity-70 transition-opacity"
          >{{ i18n.t('reader_toc') }}</button>
          <button
            @click="showAI = !showAI"
            class="text-sm hover:opacity-70 transition-opacity"
            :class="showAI ? 'text-accent' : ''"
          >{{ i18n.t('reader_ai') }}</button>
          <span v-if="chapter" class="text-sm truncate max-w-[200px]" :lang="readingLanguage">
            {{ chapterLabel(chapter) }}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <button
            v-if="auth.isAdmin && chapter"
            @click="resyncChapter"
            :disabled="chapterSyncing"
            class="text-xs px-2 py-1 rounded border transition-colors disabled:opacity-50"
            :class="isDark ? 'border-gray-700 hover:bg-gray-800' : 'border-border hover:bg-black/5'"
          >{{ chapterSyncing ? i18n.t('reader_chapter_syncing') : i18n.t('reader_chapter_resync') }}</button>
          <div v-if="alternates.length > 1" class="relative">
            <button
              @click="showSourceMenu = !showSourceMenu; showAppearance = false"
              class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-sm"
              :class="showSourceMenu ? 'text-accent' : ''"
              :title="i18n.t('reader_sources')"
            >{{ i18n.t('reader_sources_short') }}</button>
            <div
              v-if="showSourceMenu"
              class="absolute right-0 top-full mt-1 w-60 rounded-lg border shadow-lg p-2 z-50"
              :class="isDark ? 'bg-gray-800 border-gray-700' : 'bg-white border-border'"
            >
              <button
                v-for="alt in alternates"
                :key="alt.id"
                @click="switchSource(alt)"
                class="w-full text-left px-2 py-1.5 rounded text-xs transition-colors"
                :class="alt.is_current
                  ? 'bg-accent text-white font-medium'
                  : isDark ? 'hover:bg-gray-700' : 'hover:bg-gray-100'"
              >
                {{ alt.source_name || alt.source_id || i18n.t('reader_unknown') }}
                <span v-if="alt.is_current" class="ml-1 opacity-70">{{ i18n.t('reader_current') }}</span>
              </button>
            </div>
          </div>
          <span
            class="text-xs text-muted dark:text-gray-400 w-12 text-right tabular-nums"
            :title="i18n.t('reader_reading_progress')"
          >{{ i18n.t('reader_reading_position', { n: desktopProgress }) }}</span>
          <button
            @click="toggleBookmark"
            :disabled="bookmarkBusy || !chapter"
            class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-sm disabled:opacity-50"
            :class="currentBookmark() ? 'text-amber-400' : ''"
            :title="currentBookmark() ? i18n.t('reader_bookmark_remove') : i18n.t('reader_bookmark_add')"
          >{{ currentBookmark() ? '\u2605' : '\u2606' }}</button>
          <div class="relative" @keydown.esc="showAppearance = false">
            <button
              @click="showAppearance = !showAppearance; showSourceMenu = false"
              class="flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-sm transition-colors hover:bg-black/10 dark:hover:bg-white/10"
              :aria-expanded="showAppearance"
              :class="showAppearance ? 'text-accent dark:text-emerald-200' : ''"
            ><span aria-hidden="true" class="font-serif text-base font-semibold">Aa</span>{{ i18n.t('reader_appearance') }}</button>
            <div
              v-if="showAppearance"
              class="reader-appearance-popover absolute right-0 top-full z-50 mt-2 rounded-xl border p-4 shadow-xl"
              :class="isDark ? 'border-gray-700 bg-gray-800' : 'border-border bg-surface'"
            >
              <ReaderAppearancePanel
                v-model="appearance"
                :system-dark="isDark"
                :cn-fonts="cnFonts"
                :en-fonts="enFonts"
                :script-loading="scriptLoading"
                :script-failed="scriptFailed"
                @retry="scriptAttempt++"
              />
            </div>
          </div>
        </div>
      </header>

      <div
        v-if="showAppearance"
        class="fixed inset-0 z-30"
        @click="showAppearance = false"
      />

      <main class="max-w-3xl mx-auto px-4 py-10">
        <p v-if="loading" class="text-center py-16">{{ i18n.t('reader_loading') }}</p>
        <p v-else-if="error" class="text-center py-16 text-red-500">{{ error }}</p>

        <article
          v-else-if="chapter"
          ref="desktopTextRef"
          class="reader-content prose"
          :class="{ 'hide-content-images': !showContentImages }"
          :style="readerFontStyle"
          :lang="readingLanguage"
          @load.capture="onContentLoad"
        >
          <h1 class="text-2xl font-bold mb-8 text-center">
            {{ chapterLabel(chapter) }}
          </h1>
          <div v-html="chapterBodyHtml" @click="openChapterImage" />
        </article>

        <div
          v-if="chapter && (contentHasMore || contentChunkError)"
          class="mt-8 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 px-4 py-3 text-center text-sm"
        >
          <p v-if="contentChunkError" class="text-red-600 dark:text-red-400">
            {{ contentChunkError }}
          </p>
          <p v-else class="text-muted dark:text-gray-400">
            {{ i18n.t('reader_chunk_partial', {
              loaded: contentLoadedLength,
              total: contentTotalLength,
            }) }}
          </p>
          <button
            @click="loadRemainingContent"
            :disabled="contentChunkLoading"
            class="mt-2 rounded bg-accent px-4 py-2 text-xs text-white disabled:opacity-50"
          >{{ contentChunkLoading
              ? i18n.t('reader_chunk_loading')
              : i18n.t('reader_chunk_load_rest', { n: contentRemaining }) }}</button>
        </div>

        <nav
          v-if="chapter"
          class="flex items-center justify-between mt-12 pt-6 border-t"
          :class="isDark ? 'border-gray-800' : 'border-border'"
        >
          <button
            v-if="prevChapter"
            @click="openChapter(prevChapter.id)"
            class="text-sm hover:opacity-70 transition-opacity"
            :lang="readingLanguage"
          >&larr; {{ prevChapter.title ? displayText(prevChapter.title) : i18n.t('reader_ch_short', { n: prevChapter.chapter_number }) }}</button>
          <span v-else class="text-sm text-muted">{{ i18n.t('reader_start') }}</span>
          <!-- The end of a chapter is only the end once every chunk is loaded:
               saying "已经到结尾" while content was still missing is what made a
               long chapter look truncated with no way to fetch the rest. -->
          <button
            v-if="nextChapter"
            @click="openChapter(nextChapter.id)"
            class="text-sm hover:opacity-70 transition-opacity"
            :lang="readingLanguage"
          >{{ nextChapter.title ? displayText(nextChapter.title) : i18n.t('reader_ch_short', { n: nextChapter.chapter_number }) }} &rarr;</button>
          <span v-else-if="contentHasMore" class="text-sm text-muted">
            {{ i18n.t('reader_chunk_more_above') }}
          </span>
          <span v-else class="text-sm text-muted">{{ i18n.t('reader_end') }}</span>
        </nav>
      </main>
    </template>

    <Teleport to="body">
      <div
        v-if="showToc"
        class="fixed inset-0 z-[60] flex"
        @click.self="showToc = false"
      >
        <div
          class="w-72 max-w-[85vw] h-full overflow-y-auto shadow-xl p-4"
          :class="isDark ? 'bg-gray-900' : 'bg-surface'"
        >
          <div class="flex items-center justify-between mb-4">
            <div class="flex items-center gap-1">
              <button
                @click="tocTab = 'toc'"
                class="px-2 py-1 text-xs rounded transition-colors"
                :class="tocTab === 'toc' ? 'bg-accent text-white' : (isDark ? 'text-gray-400 hover:bg-gray-800' : 'text-muted hover:bg-gray-100')"
              >{{ i18n.t('reader_toc_tab_toc') }}</button>
              <button
                @click="tocTab = 'bookmarks'"
                class="px-2 py-1 text-xs rounded transition-colors"
                :class="tocTab === 'bookmarks' ? 'bg-accent text-white' : (isDark ? 'text-gray-400 hover:bg-gray-800' : 'text-muted hover:bg-gray-100')"
              >{{ i18n.t('reader_toc_tab_bookmarks') }} ({{ bookmarks.length }})</button>
            </div>
            <button @click="showToc = false" class="text-muted text-lg">&times;</button>
          </div>

          <template v-if="tocTab === 'toc'">
            <button
              v-for="ch in visibleTocChapters"
              :key="ch.id"
              @click="openChapter(ch.id)"
              class="block w-full text-left py-1.5 text-sm truncate"
              :lang="readingLanguage"
              :class="
                ch.id === chapterId
                  ? 'text-accent font-medium'
                  : isDark ? 'text-gray-400 hover:text-gray-200' : 'text-muted hover:text-ink'
              "
            >{{ ch.chapter_number }}. {{ chapterLabel(ch) }}</button>
            <div v-if="hasMoreTocChapters" ref="tocSentinel" class="py-2 text-center text-xs text-muted dark:text-gray-400">
              {{ i18n.t('book_chapters_more', { n: chapters.length - visibleTocChapters.length }) }}
            </div>
          </template>

          <template v-else>
            <p v-if="bookmarks.length === 0" class="text-xs text-muted dark:text-gray-400 py-6 text-center">{{ i18n.t('reader_bookmark_empty') }}</p>
            <div v-for="bm in bookmarks" :key="bm.id" class="flex items-center gap-2 py-1.5 border-b border-border dark:border-gray-700 last:border-0">
              <button
                @click="jumpToBookmark(bm)"
                class="min-w-0 flex-1 text-left"
                :class="isDark ? 'text-gray-300 hover:text-accent' : 'text-ink hover:text-accent'"
              >
                <span class="block truncate text-sm" :lang="readingLanguage">{{ bm.chapter_title ? displayText(bm.chapter_title) : i18n.t('reader_bookmark_chapter', { n: bm.chapter_number || '' }) }}</span>
                <span class="block text-xs text-muted dark:text-gray-400">{{ i18n.t('reader_bookmark_position', { p: bm.position }) }}</span>
              </button>
              <button
                @click="removeBookmark(bm.id)"
                class="shrink-0 text-xs text-red-400 hover:text-red-600 px-1.5"
                :title="i18n.t('reader_bookmark_delete')"
              >&times;</button>
            </div>
          </template>
        </div>
        <div class="flex-1" @click="showToc = false" />
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="showAI"
        class="fixed inset-0 z-[60] flex justify-end"
        @click.self="showAI = false"
      >
        <div
          class="w-96 max-w-[92vw] h-full shadow-xl flex flex-col"
          :class="isDark ? 'bg-gray-900' : 'bg-surface'"
        >
          <div class="flex items-center justify-between px-4 py-2 border-b" :class="isDark ? 'border-gray-800' : 'border-border'">
            <span class="text-sm font-medium">{{ i18n.t('reader_ai_title') }}</span>
            <button @click="showAI = false" class="text-muted text-lg">&times;</button>
          </div>
          <div class="flex-1 overflow-hidden">
            <AIChat
              ref="aiChatRef"
              :bookId="bookId"
              :chapter-number="chapter?.chapter_number ?? null"
              @open-settings="openAISettings"
            />
          </div>
        </div>
        <div class="flex-1" @click="showAI = false" />
      </div>
    </Teleport>

    <AISelectionToolbar
      :book-id="bookId"
      :chapter-number="chapter?.chapter_number ?? null"
      :container="aiSelectionContainer"
      @ask="askAIAbout"
    />
    <ImageViewer
      v-if="viewedImage"
      :src="viewedImage.src"
      :alt="viewedImage.alt"
      @close="viewedImage = null"
    />
  </div>
</template>

<style scoped>
.reader-canvas,
.mobile-reader {
  background: var(--reader-background);
  color: var(--reader-foreground);
}

.prose :deep(p),
.page-body :deep(p) {
  text-indent: var(--reader-paragraph-indent);
  margin-bottom: 0.75em;
  line-height: var(--reader-line-height);
}

.reader-appearance-popover {
  width: min(24rem, calc(100vw - 2rem));
  max-height: calc(100dvh - 4.5rem);
  overflow: auto;
  overscroll-behavior: contain;
}

.mobile-reader {
  --quickbar-height: calc(3rem + env(safe-area-inset-top));
  position: fixed;
  inset: 0;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
}

.page-surface {
  position: absolute;
  inset: var(--quickbar-height) 0 0;
  overflow: hidden;
  touch-action: pan-y;
  user-select: none;
  -webkit-user-select: none;
  -webkit-tap-highlight-color: transparent;
}

.page-viewport {
  position: absolute;
  inset: 0;
  overflow: hidden;
}

.page-columns {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  column-width: 100vw;
  column-gap: 32px;
  column-fill: auto;
  will-change: transform;
}

/* Multicol pagination: an unbreakable box taller than the column overflows on
   top of the NEXT page, so the reader saw "the last line of page N printed over
   the first line of page N+1" (reported 2026-10-21 on a chapter with images).
   Comic pages are 2-3x the viewport, and markdown links can hold a very long
   word, so cap both here instead of trusting the source markup. */
.reader-content :deep(.chapter-image-trigger),
.page-body :deep(.chapter-image-trigger) {
  display: block;
  width: fit-content;
  max-width: 100%;
  margin: 0.75rem auto;
  text-indent: 0;
  cursor: zoom-in;
  break-inside: avoid;
  page-break-inside: avoid;
}

.reader-content :deep(.chapter-image-trigger img),
.page-body :deep(.chapter-image-trigger img) {
  display: block;
  max-width: 100%;
  height: auto;
  margin-inline: auto;
  object-fit: contain;
}

.hide-content-images :deep(.chapter-image-trigger),
.hide-content-images :deep(.reader-illustration) {
  display: none;
}

.page-columns :deep(.chapter-image-trigger img) {
  max-height: calc(100vh - 190px);
  max-height: max(70px, calc(100dvh - var(--quickbar-height) - 145px));
}

.page-columns :deep(p),
.page-columns :deep(blockquote),
.page-columns :deep(li) {
  overflow-wrap: anywhere;
  word-break: break-word;
}

.page-mode-cover .page-columns {
  transition: transform 0.22s cubic-bezier(0.2, 0.7, 0.25, 1);
  filter: drop-shadow(-12px 0 10px rgba(0, 0, 0, 0.08));
}

.page-mode-slide .page-columns {
  transition: transform 0.3s cubic-bezier(0.22, 1, 0.36, 1);
}

.page-mode-simulation .page-viewport {
  perspective: 1200px;
}

.page-mode-simulation .page-columns {
  transform-style: preserve-3d;
  transform-origin: center right;
  transition: transform 0.42s cubic-bezier(0.34, 0.03, 0.18, 1);
  filter: drop-shadow(-16px 2px 12px rgba(0, 0, 0, 0.13));
}

.page-mode-none .page-columns {
  transition: none;
}

.page-inner {
  min-height: 100%;
  padding: 24px 22px 42px;
  box-sizing: border-box;
}

.page-title {
  font-size: 1.35em;
  font-weight: 700;
  text-align: center;
  line-height: 1.5;
  margin: 0 0 1.1em;
}

.page-meta {
  position: absolute;
  left: 50%;
  bottom: 8px;
  transform: translateX(-50%);
  z-index: 5;
  font-size: 12px;
  opacity: 0.45;
  pointer-events: none;
}

/* Long chapters arrive in chunks; this is the visible way to pull the rest
   (the background prefetch normally finishes first and hides it again). */
.page-chunk-hint {
  position: absolute;
  left: 50%;
  bottom: max(30px, calc(env(safe-area-inset-bottom) + 26px));
  transform: translateX(-50%);
  z-index: 6;
  padding: 7px 14px;
  border-radius: 999px;
  border: 1px solid rgba(128, 128, 128, 0.35);
  background: rgba(128, 128, 128, 0.16);
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
}

.page-chunk-hint[disabled] {
  opacity: 0.5;
}

.page-chunk-hint-scroll {
  position: sticky;
  bottom: max(12px, env(safe-area-inset-bottom));
  left: auto;
  transform: none;
  width: max-content;
  margin: 16px auto 0;
}

.scroll-page-surface {
  position: absolute;
  inset: var(--quickbar-height) 0 0;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior-y: contain;
  -webkit-overflow-scrolling: touch;
  -webkit-tap-highlight-color: transparent;
}

.scroll-page-content {
  box-sizing: border-box;
  min-height: 100%;
  padding: 24px 22px max(52px, env(safe-area-inset-bottom));
  line-height: var(--reader-line-height);
}

.scroll-page-meta {
  position: sticky;
  bottom: max(8px, env(safe-area-inset-bottom));
  width: max-content;
  margin: 0 auto;
  padding: 2px 7px;
  border-radius: 4px;
  background: rgba(90, 90, 90, 0.12);
  font-size: 11px;
  opacity: 0.7;
  pointer-events: none;
}

.reader-menu-layer {
  background: transparent;
  --reader-control-accent: #236454;
  --reader-control-ink: #fff;
  --reader-control-surface: #fbf9f3;
  --reader-control-text: #202924;
}

.reader-menu-dark {
  --reader-control-accent: #94cbb6;
  --reader-control-ink: #10251e;
  --reader-control-surface: #1b2522;
  --reader-control-text: #e5eae6;
}

.reader-quickbar {
  position: absolute;
  inset: 0 0 auto;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--quickbar-height);
  padding: env(safe-area-inset-top) max(0.75rem, env(safe-area-inset-right)) 0 max(0.75rem, env(safe-area-inset-left));
  border-bottom: 1px solid rgba(128, 128, 128, 0.2);
}

.reader-quick-action {
  min-height: 2.75rem;
  padding-inline: 0.5rem;
  font-size: 0.875rem;
}

.reader-topbar,
.reader-bottombar,
.reader-bottom-panel {
  background: var(--reader-control-surface);
  color: var(--reader-control-text);
}

.reader-topbar,
.reader-bottombar {
  position: absolute;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}

.reader-topbar {
  top: 0;
  min-height: 3.25rem;
  gap: 0.25rem;
  padding: max(0.45rem, env(safe-area-inset-top)) 0.75rem 0.45rem;
  border-bottom: 1px solid rgba(128, 128, 128, 0.25);
}

.reader-bottombar {
  bottom: 0;
  flex-direction: column;
  align-items: stretch;
  gap: 0;
  padding-bottom: env(safe-area-inset-bottom);
  border-top: 1px solid rgba(128, 128, 128, 0.25);
}

.reader-top-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.25rem;
  height: 2.25rem;
  flex: 0 0 2.25rem;
  border-radius: 4px;
  font-size: 1.15rem;
  transition: background-color 0.15s ease;
}

.reader-top-action:active,
.reader-action-btn:active,
.reader-chapter-btn:active {
  background: rgba(128, 128, 128, 0.12);
}

.reader-top-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.8rem;
  opacity: 0.75;
  text-align: center;
}

.reader-bottom-panel {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 100%;
  z-index: 2;
  max-height: min(56vh, 28rem);
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 0.85rem 1rem 1rem;
  border-top: 1px solid rgba(128, 128, 128, 0.25);
  border-radius: 1rem 1rem 0 0;
  box-shadow: 0 -10px 25px rgba(0, 0, 0, 0.12);
}

.reader-chapter-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.7rem;
  min-height: 3.25rem;
  padding: 0.25rem 1rem;
}

.reader-chapter-btn {
  min-width: 3.25rem;
  min-height: 2.5rem;
  font-size: 0.8rem;
}

.reader-chapter-btn:disabled {
  opacity: 0.3;
}

.reader-progress-wrap {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 3.2rem;
  align-items: center;
  gap: 0.45rem;
  min-width: 0;
  font-size: 0.7rem;
  text-align: center;
  opacity: 0.75;
}

.reader-progress {
  width: 100%;
  min-width: 0;
  accent-color: var(--reader-control-accent);
}

.reader-actions {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border-top: 1px solid rgba(128, 128, 128, 0.2);
}

.reader-action-btn {
  display: flex;
  min-width: 0;
  min-height: 3.75rem;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.15rem;
  padding: 0.35rem 0.1rem;
  font-size: 0.65rem;
}

.reader-action-icon {
  display: flex;
  width: 1.5rem;
  height: 1.5rem;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  line-height: 1;
}

.reader-panel-label {
  margin: 0.7rem 0 0.4rem;
  font-size: 0.7rem;
  opacity: 0.65;
}

.reader-mode-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.3rem;
}

.tap-area-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.35rem;
}

.tap-area-cell {
  display: flex;
  min-height: 3.1rem;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.1rem;
  overflow: hidden;
  padding: 0.3rem 0.2rem;
  border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 4px;
}

.tap-area-cell-active {
  border-color: var(--reader-control-accent);
  box-shadow: inset 0 0 0 1px var(--reader-control-accent);
}

.tap-area-cell-menu {
  background: color-mix(in srgb, var(--reader-control-accent) 14%, transparent);
}

.tap-area-cell-none {
  opacity: 0.55;
}

.tap-area-cell-name {
  font-size: 0.6rem;
  line-height: 1;
  opacity: 0.6;
}

.tap-area-cell-action {
  max-width: 100%;
  font-size: 0.7rem;
  line-height: 1.2;
  text-align: center;
}

.tap-action-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.35rem;
}

.tap-area-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.7rem;
}

.reader-tap-reset-btn,
.reader-tap-save-btn {
  min-height: 2.1rem;
  padding: 0.35rem 0.7rem;
  border-radius: 4px;
  font-size: 0.72rem;
}

.reader-tap-reset-btn {
  border: 1px solid rgba(128, 128, 128, 0.3);
}

.reader-tap-save-btn {
  background: var(--reader-control-accent);
  color: var(--reader-control-ink);
}

.reader-tap-save-btn:disabled {
  opacity: 0.4;
}

.reader-option-btn {
  min-width: 0;
  min-height: 2.25rem;
  overflow: hidden;
  padding: 0.35rem 0.4rem;
  border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 4px;
  font-size: 0.72rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reader-option-active {
  border-color: var(--reader-control-accent);
  background: var(--reader-control-accent);
  color: var(--reader-control-ink);
}

.bookmark-toast {
  position: fixed;
  top: 14%;
  left: 50%;
  transform: translateX(-50%);
  z-index: 90;
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 13px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.bookmark-toast-light {
  background: rgba(31, 41, 55, 0.92);
  color: #f9fafb;
}

.bookmark-toast-dark {
  background: rgba(243, 244, 246, 0.92);
  color: #111827;
}

@media (max-width: 360px) {
  .reader-chapter-row {
    gap: 0.35rem;
    padding-inline: 0.5rem;
  }

  .reader-progress-wrap {
    grid-template-columns: minmax(0, 1fr) 2.7rem;
  }

  .reader-action-btn {
    font-size: 0.6rem;
  }
}
</style>
