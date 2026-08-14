<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useBooksStore, type Chapter, type ChapterContent } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import { api } from "../api/client"
import AIChat from "../components/AIChat.vue"
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
const loading = ref(true)
const error = ref("")
const fontSize = ref(18)
const cnFont = ref(localStorage.getItem("novelhub_cn_font") || "default")
const enFont = ref(localStorage.getItem("novelhub_en_font") || "default")
const showFontMenu = ref(false)
const isDark = ref(localStorage.getItem("novelhub_dark") === "true")
const showToc = ref(false)
const showAI = ref(false)
const chapterSyncing = ref(false)
const alternates = ref<any[]>([])
const showSourceMenu = ref(false)

const showContentImages = computed(() => auth.user?.settings?.show_content_images !== false)

const isMobileLayout = ref(false)
const menuVisible = ref(false)
const currentPage = ref(0)
const pageCount = ref(1)
const pageMode = ref<MobilePageMode>(savedPageMode())
const mobileScrollProgress = ref(0)
const pendingPage = ref<number | "last" | null>(null)
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
  const cn = cnFontStack[cnFont.value] || ""
  const en = enFontStack[enFont.value] || ""
  const stack = [en, cn].filter(Boolean).join(", ")
  return {
    fontSize: fontSize.value + "px",
    ...(stack ? { fontFamily: stack } : {}),
  }
})

const chapterBodyHtml = computed(() => {
  if (!chapter.value) return ""
  let html = chapter.value.content
    .replace(
      /!\[([^\]]*)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g,
      '<img src="$2" alt="$1" loading="lazy">',
    )
  html = html
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br>")
  return "<p>" + html + "</p>"
})

const pageProgress = computed(() => {
  if (pageCount.value <= 1) return 100
  return Math.round((currentPage.value / (pageCount.value - 1)) * 100)
})
const mobileProgress = computed(() => pageMode.value === "scroll" ? mobileScrollProgress.value : pageProgress.value)
const mobileProgressLabel = computed(() => pageMode.value === "scroll"
  ? `${mobileScrollProgress.value}%`
  : `${currentPage.value + 1} / ${pageCount.value}`)

function savePosition(position: number) {
  if (!auth.user || !chapter.value) return
  api.put("/progress", {
    user_id: auth.user.id,
    book_id: bookId.value,
    chapter_id: chapterId.value,
    position,
  }).catch(() => {})
}

function onScroll() {
  clearTimeout(scrollTimer)
  scrollTimer = setTimeout(() => {
    if (!auth.user || isMobileLayout.value) return
    const scrollPercent = Math.round(
      (window.scrollY / (document.documentElement.scrollHeight - window.innerHeight)) * 100
    )
    savePosition(scrollPercent)
  }, 2000)
}

function toggleDark() {
  isDark.value = !isDark.value
  localStorage.setItem("novelhub_dark", String(isDark.value))
  document.documentElement.classList.toggle("dark", isDark.value)
}

function changeFontSize(delta: number) {
  fontSize.value = Math.min(26, Math.max(14, fontSize.value + delta))
  if (isMobileLayout.value) nextTick(refreshMobileLayout)
}

function setCnFont(f: string) {
  cnFont.value = f
  localStorage.setItem("novelhub_cn_font", f)
  if (isMobileLayout.value) nextTick(refreshMobileLayout)
}

function setEnFont(f: string) {
  enFont.value = f
  localStorage.setItem("novelhub_en_font", f)
  if (isMobileLayout.value) nextTick(refreshMobileLayout)
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
  if (isMobileLayout.value) savePosition(mobileProgress.value)
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
    if (nextChapter.value) openChapter(nextChapter.value.id, 0)
    return
  }
  if (currentPage.value < pageCount.value - 1) {
    currentPage.value += 1
    applyPageTransform()
    queuePageProgress()
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
  const touch = event.changedTouches[0]
  touchStartX = touch.clientX
  touchStartY = touch.clientY
  touchStartTime = Date.now()
  suppressClick = false
}

function onTouchEnd(event: TouchEvent) {
  if (menuVisible.value || pageMode.value === "scroll") return
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
  showFontMenu.value = false
  showSourceMenu.value = false
  showPageModeMenu.value = false
  showTapAreaMenu.value = false
}

function goBackToBook() {
  const back = (window.history.state as { back?: string | null } | null)?.back
  if (back) {
    router.back()
  } else {
    router.replace("/books/" + bookId.value)
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

async function loadChapter(id: string) {
  loading.value = true
  error.value = ""
  currentPage.value = 0
  pageCount.value = 1
  try {
    chapter.value = await store.fetchChapter(id)
    loading.value = false
    if (isMobileLayout.value) await refreshMobileLayout()
  } catch (e) {
    error.value = e instanceof Error ? e.message : i18n.t('reader_failed_load_chapter')
  } finally {
    loading.value = false
  }
}

async function resyncChapter() {
  if (!chapter.value) return
  chapterSyncing.value = true
  try {
    await api.post('/chapters/' + chapter.value.id + '/sync')
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

watch([fontSize, cnFont, enFont], () => {
  if (isMobileLayout.value) nextTick(refreshMobileLayout)
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

watch(
  () => route.params.bookId,
  async (newId) => {
    if (!newId) return
    try {
      chapters.value = await store.fetchChapters(newId as string)
    } catch { /* non-fatal */ }
    await loadAlternates()
    if (isMobileLayout.value) nextTick(refreshMobileLayout)
  },
)

onMounted(async () => {
  document.documentElement.classList.toggle("dark", isDark.value)
  window.addEventListener("scroll", onScroll, { passive: true })
  window.addEventListener("resize", onResize, { passive: true })
  mediaQuery = window.matchMedia("(max-width: 900px) and (pointer: coarse), (max-width: 700px)")
  mediaListener = () => updateMobileLayout()
  mediaQuery.addEventListener?.("change", mediaListener)
  updateMobileLayout()
  try {
    chapters.value = await store.fetchChapters(bookId.value)
  } catch { /* non-fatal */ }
  await loadAlternates()
  await loadChapter(chapterId.value)
})

onUnmounted(() => {
  window.removeEventListener("scroll", onScroll)
  window.removeEventListener("resize", onResize)
  mediaQuery?.removeEventListener?.("change", mediaListener as EventListener)
  document.documentElement.classList.remove("reader-locked")
  clearTimeout(scrollTimer)
  clearTimeout(resizeTimer)
  flushPageProgress()
})
</script>

<template>
  <div class="min-h-screen" :class="isDark ? 'bg-gray-950 text-gray-100' : 'bg-paper text-ink'">
    <div v-if="isMobileLayout" class="mobile-reader" :class="isDark ? 'mobile-reader-dark' : 'mobile-reader-light'">
      <p
        v-if="loading"
        class="absolute inset-0 flex items-center justify-center text-sm text-muted dark:text-gray-400"
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
          @load.capture="nextTick(refreshMobileLayout)"
        >
          <div ref="pageViewport" class="page-viewport">
            <article ref="pageContent" class="page-columns" :style="readerFontStyle">
              <div class="page-inner">
                <h1 class="page-title">
                  {{ chapter.title || i18n.t('reader_chapter_fallback', { n: chapter.chapter_number }) }}
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
        </div>

        <div
          v-else
          ref="scrollViewport"
          class="scroll-page-surface"
          @scroll.passive="onMobileScroll"
          @click="handleTap"
          @load.capture="updateMobileScrollProgress"
        >
          <article class="scroll-page-content" :style="readerFontStyle">
            <h1 class="page-title">
              {{ chapter.title || i18n.t('reader_chapter_fallback', { n: chapter.chapter_number }) }}
            </h1>
            <div
              class="page-body"
              :class="{ 'hide-content-images': !showContentImages }"
              v-html="chapterBodyHtml"
            />
          </article>
          <div class="scroll-page-meta">{{ mobileScrollProgress }}%</div>
        </div>

        <div
          v-if="menuVisible"
          class="fixed inset-0 z-50 reader-menu-layer"
          :class="isDark ? 'reader-menu-dark' : 'reader-menu-light'"
          @click.self="closeMenu"
        >
          <div
            v-if="showFontMenu || showSourceMenu || showPageModeMenu || showTapAreaMenu"
            class="fixed inset-0"
            @click="closeMenu"
          />
          <header class="reader-topbar" @click.stop>
            <button @click="goBackToBook" class="reader-top-action" :title="i18n.t('reader_book')">&larr;</button>
            <span class="reader-top-title">
              {{ chapter.title || i18n.t('reader_chapter_fallback', { n: chapter.chapter_number }) }}
            </span>
            <button
              v-if="alternates.length > 1"
              @click="showSourceMenu = !showSourceMenu; showFontMenu = false; showPageModeMenu = false; showTapAreaMenu = false"
              class="reader-top-action text-xs"
              :class="showSourceMenu ? 'text-accent' : ''"
              :title="i18n.t('reader_sources')"
            >{{ i18n.t('reader_sources_short') }}</button>
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

            <div v-if="showFontMenu" class="reader-bottom-panel">
              <div class="reader-font-size-row">
                <button @click="changeFontSize(-2)" class="reader-size-btn" :title="i18n.t('reader_smaller_font')">A-</button>
                <span class="text-sm">{{ fontSize }}px</span>
                <button @click="changeFontSize(2)" class="reader-size-btn" :title="i18n.t('reader_larger_font')">A+</button>
              </div>
              <p class="reader-panel-label">{{ i18n.t('reader_cn_font') }}</p>
              <div class="reader-option-grid">
                <button v-for="f in cnFonts" :key="f.value" @click="setCnFont(f.value)" class="reader-option-btn" :class="cnFont === f.value ? 'reader-option-active' : ''">{{ f.label }}</button>
              </div>
              <p class="reader-panel-label">{{ i18n.t('reader_en_font') }}</p>
              <div class="reader-option-grid">
                <button v-for="f in enFonts" :key="f.value" @click="setEnFont(f.value)" class="reader-option-btn" :class="enFont === f.value ? 'reader-option-active' : ''">{{ f.label }}</button>
              </div>
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
              <button @click="showFontMenu = !showFontMenu; showPageModeMenu = false; showSourceMenu = false; showTapAreaMenu = false" class="reader-action-btn" :class="showFontMenu ? 'text-accent' : ''"><span class="reader-action-icon font-serif">Aa</span><span>{{ i18n.t('reader_interface') }}</span></button>
              <button @click="showPageModeMenu = !showPageModeMenu; showFontMenu = false; showSourceMenu = false; showTapAreaMenu = false" class="reader-action-btn" :class="showPageModeMenu ? 'text-accent' : ''"><span class="reader-action-icon">&#8596;</span><span>{{ i18n.t('reader_page_mode') }}</span></button>
              <button @click="showTapAreaMenu = !showTapAreaMenu; showFontMenu = false; showSourceMenu = false; showPageModeMenu = false" class="reader-action-btn" :class="showTapAreaMenu ? 'text-accent' : ''"><span class="reader-action-icon">&#9638;</span><span>{{ i18n.t('reader_tap_area_short') }}</span></button>
              <button @click="toggleDark" class="reader-action-btn"><span class="reader-action-icon">{{ isDark ? '\u2600' : '\u263e' }}</span><span>{{ isDark ? i18n.t('reader_light_mode_short') : i18n.t('reader_dark_mode_short') }}</span></button>
            </nav>
          </footer>
        </div>
      </template>
    </div>

    <template v-else>
      <header
        class="sticky top-0 z-40 border-b h-12 flex items-center justify-between px-4"
        :class="isDark ? 'bg-gray-900/90 border-gray-800' : 'bg-surface/80 border-border'"
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
          <span v-if="chapter" class="text-sm truncate max-w-[200px]">
            {{ chapter.title || i18n.t('reader_chapter_fallback', { n: chapter.chapter_number }) }}
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
              @click="showSourceMenu = !showSourceMenu"
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
          <button
            @click="changeFontSize(-2)"
            class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-sm"
            :title="i18n.t('reader_smaller_font')"
          >A-</button>
          <button
            @click="changeFontSize(2)"
            class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-sm"
            :title="i18n.t('reader_larger_font')"
          >A+</button>
          <div class="relative">
            <button
              @click="showFontMenu = !showFontMenu"
              class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-sm"
              :class="(cnFont !== 'default' || enFont !== 'default') ? 'text-accent' : ''"
              :title="i18n.t('reader_font_settings')"
            >F</button>
            <div
              v-if="showFontMenu"
              class="absolute right-0 top-full mt-1 w-48 rounded-lg border shadow-lg p-3 z-50"
              :class="isDark ? 'bg-gray-800 border-gray-700' : 'bg-white border-border'"
            >
              <div class="text-xs font-medium mb-2 text-muted dark:text-gray-400">{{ i18n.t('reader_cn_font') }}</div>
              <div class="flex flex-wrap gap-1 mb-3">
                <button
                  v-for="f in cnFonts"
                  :key="f.value"
                  @click="setCnFont(f.value)"
                  class="text-xs px-2 py-1 rounded transition-colors"
                  :class="cnFont === f.value
                    ? 'bg-accent text-white'
                    : isDark ? 'bg-gray-700 hover:bg-gray-600' : 'bg-gray-100 hover:bg-gray-200'"
                >{{ f.label }}</button>
              </div>
              <div class="text-xs font-medium mb-2 text-muted dark:text-gray-400">{{ i18n.t('reader_en_font') }}</div>
              <div class="flex flex-wrap gap-1">
                <button
                  v-for="f in enFonts"
                  :key="f.value"
                  @click="setEnFont(f.value)"
                  class="text-xs px-2 py-1 rounded transition-colors"
                  :class="enFont === f.value
                    ? 'bg-accent text-white'
                    : isDark ? 'bg-gray-700 hover:bg-gray-600' : 'bg-gray-100 hover:bg-gray-200'"
                >{{ f.label }}</button>
              </div>
            </div>
          </div>
          <button
            @click="toggleDark"
            class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-sm"
            :title="isDark ? i18n.t('reader_light_mode') : i18n.t('reader_dark_mode')"
          >{{ isDark ? '\u2600' : '\u263e' }}</button>
        </div>
      </header>

      <div
        v-if="showFontMenu"
        class="fixed inset-0 z-30"
        @click="showFontMenu = false"
      />

      <main class="max-w-3xl mx-auto px-4 py-10">
        <p v-if="loading" class="text-center py-16">{{ i18n.t('reader_loading') }}</p>
        <p v-else-if="error" class="text-center py-16 text-red-500">{{ error }}</p>

        <article
          v-else-if="chapter"
          class="reader-content prose"
          :class="{ 'hide-content-images': !showContentImages }"
          :style="readerFontStyle"
        >
          <h1 class="text-2xl font-bold mb-8 text-center">
            {{ chapter.title || i18n.t('reader_chapter_fallback', { n: chapter.chapter_number }) }}
          </h1>
          <div v-html="chapterBodyHtml" />
        </article>

        <nav
          v-if="chapter"
          class="flex items-center justify-between mt-12 pt-6 border-t"
          :class="isDark ? 'border-gray-800' : 'border-border'"
        >
          <button
            v-if="prevChapter"
            @click="openChapter(prevChapter.id)"
            class="text-sm hover:opacity-70 transition-opacity"
          >&larr; {{ prevChapter.title || i18n.t('reader_ch_short', { n: prevChapter.chapter_number }) }}</button>
          <span v-else class="text-sm text-muted">{{ i18n.t('reader_start') }}</span>
          <button
            v-if="nextChapter"
            @click="openChapter(nextChapter.id)"
            class="text-sm hover:opacity-70 transition-opacity"
          >{{ nextChapter.title || i18n.t('reader_ch_short', { n: nextChapter.chapter_number }) }} &rarr;</button>
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
            <h3 class="font-semibold text-sm">{{ i18n.t('reader_toc_title') }}</h3>
            <button @click="showToc = false" class="text-muted text-lg">&times;</button>
          </div>
          <button
            v-for="ch in chapters"
            :key="ch.id"
            @click="openChapter(ch.id)"
            class="block w-full text-left py-1.5 text-sm truncate"
            :class="
              ch.id === chapterId
                ? 'text-accent font-medium'
                : isDark ? 'text-gray-400 hover:text-gray-200' : 'text-muted hover:text-ink'
            "
          >{{ ch.chapter_number }}. {{ ch.title || i18n.t('reader_chapter_fallback', { n: ch.chapter_number }) }}</button>
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
          class="w-80 max-w-[92vw] h-full shadow-xl flex flex-col"
          :class="isDark ? 'bg-gray-900' : 'bg-surface'"
        >
          <div class="flex items-center justify-between px-4 py-2 border-b" :class="isDark ? 'border-gray-800' : 'border-border'">
            <span class="text-sm font-medium">{{ i18n.t('reader_ai_title') }}</span>
            <button @click="showAI = false" class="text-muted text-lg">&times;</button>
          </div>
          <div class="flex-1 overflow-hidden">
            <AIChat :bookId="bookId" />
          </div>
        </div>
        <div class="flex-1" @click="showAI = false" />
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.prose p {
  text-indent: 2em;
  margin-bottom: 0.75em;
  line-height: 1.8;
}

.mobile-reader {
  position: fixed;
  inset: 0;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
}

.mobile-reader-light {
  background: #faf8f5;
  color: #1f2937;
}

.mobile-reader-dark {
  background: #111318;
  color: #d1d5db;
}

.page-surface {
  position: absolute;
  inset: 0;
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

.page-body {
  line-height: 1.9;
}

.page-body p {
  text-indent: 2em;
  margin: 0 0 0.75em;
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

.scroll-page-surface {
  position: absolute;
  inset: 0;
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
  line-height: 1.9;
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
}

.reader-menu-light .reader-topbar,
.reader-menu-light .reader-bottombar,
.reader-menu-light .reader-bottom-panel {
  background: rgba(255, 255, 255, 0.97);
  color: #1f2937;
}

.reader-menu-dark .reader-topbar,
.reader-menu-dark .reader-bottombar,
.reader-menu-dark .reader-bottom-panel {
  background: rgba(24, 27, 33, 0.97);
  color: #e5e7eb;
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
  padding: 0.85rem 1rem 1rem;
  border-top: 1px solid rgba(128, 128, 128, 0.25);
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
  accent-color: #8b5cf6;
}

.reader-actions {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
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

.reader-font-size-row {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 1rem;
  margin-bottom: 0.8rem;
}

.reader-size-btn {
  height: 2.25rem;
  border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 4px;
}

.reader-panel-label {
  margin: 0.7rem 0 0.4rem;
  font-size: 0.7rem;
  opacity: 0.65;
}

.reader-option-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.35rem;
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
  border-color: #8b5cf6;
  box-shadow: inset 0 0 0 1px #8b5cf6;
}

.tap-area-cell-menu {
  background: rgba(139, 92, 246, 0.14);
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
  background: #8b5cf6;
  color: white;
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
  border-color: #8b5cf6;
  background: #8b5cf6;
  color: white;
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
