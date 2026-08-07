<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useBooksStore, type Chapter, type ChapterContent } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import { api } from "../api/client"
import AIChat from "../components/AIChat.vue"

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

const isMobileLayout = ref(false)
const menuVisible = ref(false)
const currentPage = ref(0)
const pageCount = ref(1)
const pendingPage = ref<number | "last" | null>(null)
const pageContent = ref<HTMLElement | null>(null)
const pageViewport = ref<HTMLElement | null>(null)

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
  const html = chapter.value.content
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br>")
  return "<p>" + html + "</p>"
})

const pageProgress = computed(() => {
  if (pageCount.value <= 1) return 100
  return Math.round((currentPage.value / (pageCount.value - 1)) * 100)
})

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
  if (isMobileLayout.value) nextTick(measurePages)
}

function setCnFont(f: string) {
  cnFont.value = f
  localStorage.setItem("novelhub_cn_font", f)
  if (isMobileLayout.value) nextTick(measurePages)
}

function setEnFont(f: string) {
  enFont.value = f
  localStorage.setItem("novelhub_en_font", f)
  if (isMobileLayout.value) nextTick(measurePages)
}

function applyPageTransform() {
  const el = pageContent.value
  if (!el) return
  const styles = getComputedStyle(el)
  const columnWidth = parseFloat(styles.columnWidth) || el.clientWidth
  const gap = parseFloat(styles.columnGap) || 32
  el.style.transform = `translateX(${-(currentPage.value * (columnWidth + gap))}px)`
}

async function measurePages() {
  await nextTick()
  const el = pageContent.value
  if (!el || !isMobileLayout.value) return
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

function queuePageProgress() {
  clearTimeout(progressTimer)
  progressTimer = setTimeout(() => {
    if (isMobileLayout.value) savePosition(pageProgress.value)
  }, 800)
}

function flushPageProgress() {
  clearTimeout(progressTimer)
  if (isMobileLayout.value) savePosition(pageProgress.value)
}

function openChapter(id: string, page: number | "last" = 0, targetBookId = bookId.value) {
  closeMenu()
  showToc.value = false
  showAI.value = false
  pendingPage.value = page
  if (id === chapterId.value && targetBookId === bookId.value) {
    if (isMobileLayout.value) nextTick(measurePages)
    return
  }
  router.replace("/books/" + targetBookId + "/chapters/" + id)
}

function nextPageOrChapter() {
  if (currentPage.value < pageCount.value - 1) {
    currentPage.value += 1
    applyPageTransform()
    queuePageProgress()
    return
  }
  if (nextChapter.value) openChapter(nextChapter.value.id, 0)
}

function prevPageOrChapter() {
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
  const width = window.innerWidth || 1
  const height = window.innerHeight || 1
  const xr = event.clientX / width
  const yr = event.clientY / height
  const left = xr < 1 / 3
  const right = xr > 2 / 3
  const middle = !left && !right
  if (left || (middle && yr < 1 / 3)) {
    prevPageOrChapter()
    return
  }
  if (right || (middle && yr > 2 / 3)) {
    nextPageOrChapter()
    return
  }
  if (middle) menuVisible.value = true
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
  if (menuVisible.value) return
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
  const next = window.matchMedia("(pointer: coarse), (max-width: 820px)").matches
  if (next === isMobileLayout.value) return
  isMobileLayout.value = next
  document.documentElement.classList.toggle("reader-locked", next)
  if (next) {
    currentPage.value = 0
    nextTick(measurePages)
  }
}

function onResize() {
  clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => {
    if (isMobileLayout.value) nextTick(measurePages)
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
    if (isMobileLayout.value) await measurePages()
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
  if (isMobileLayout.value) nextTick(measurePages)
})

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
    if (isMobileLayout.value) nextTick(measurePages)
  },
)

onMounted(async () => {
  document.documentElement.classList.toggle("dark", isDark.value)
  window.addEventListener("scroll", onScroll, { passive: true })
  window.addEventListener("resize", onResize, { passive: true })
  mediaQuery = window.matchMedia("(pointer: coarse), (max-width: 820px)")
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
    <div v-if="isMobileLayout" class="mobile-reader">
      <p
        v-if="loading"
        class="absolute inset-0 flex items-center justify-center text-sm text-muted dark:text-gray-400"
      >{{ i18n.t('reader_loading') }}</p>
      <p v-else-if="error" class="absolute inset-0 flex items-center justify-center px-8 text-sm text-red-500">{{ error }}</p>

      <template v-else-if="chapter">
        <div
          class="page-surface"
          @touchstart.passive="onTouchStart"
          @touchend="onTouchEnd"
          @click="handleTap"
        >
          <div ref="pageViewport" class="page-viewport">
            <article ref="pageContent" class="page-columns" :style="readerFontStyle">
              <div class="page-inner">
                <h1 class="page-title">
                  {{ chapter.title || i18n.t('reader_chapter_fallback', { n: chapter.chapter_number }) }}
                </h1>
                <div class="page-body" v-html="chapterBodyHtml" />
              </div>
            </article>
          </div>
          <div class="page-meta">{{ currentPage + 1 }} / {{ pageCount }}</div>
        </div>

        <div
          v-if="menuVisible"
          class="fixed inset-0 z-50 reader-menu-layer"
          @click.self="closeMenu"
        >
          <div
            v-if="showFontMenu || showSourceMenu"
            class="fixed inset-0"
            @click="closeMenu"
          />
          <header class="reader-topbar" @click.stop>
            <button @click="goBackToBook" class="reader-icon-btn">&larr; {{ i18n.t('reader_book') }}</button>
            <button @click="showToc = !showToc" class="reader-icon-btn">{{ i18n.t('reader_toc') }}</button>
            <button
              @click="showAI = !showAI"
              class="reader-icon-btn"
              :class="showAI ? 'text-accent' : ''"
            >{{ i18n.t('reader_ai') }}</button>
            <span class="reader-top-title">
              {{ chapter.title || i18n.t('reader_chapter_fallback', { n: chapter.chapter_number }) }}
            </span>
            <button
              v-if="auth.isAdmin"
              @click="resyncChapter"
              :disabled="chapterSyncing"
              class="reader-icon-btn text-xs disabled:opacity-50"
            >{{ chapterSyncing ? i18n.t('reader_chapter_syncing') : i18n.t('reader_chapter_resync') }}</button>
            <button @click="closeMenu" class="reader-icon-btn">&times;</button>
          </header>

          <footer class="reader-bottombar" @click.stop>
            <div class="flex items-center gap-1">
              <button
                @click="changeFontSize(-2)"
                class="reader-icon-btn"
                :title="i18n.t('reader_smaller_font')"
              >A-</button>
              <button
                @click="changeFontSize(2)"
                class="reader-icon-btn"
                :title="i18n.t('reader_larger_font')"
              >A+</button>
              <div v-if="alternates.length > 1" class="relative">
                <button
                  @click="showSourceMenu = !showSourceMenu"
                  class="reader-icon-btn"
                  :class="showSourceMenu ? 'text-accent' : ''"
                  :title="i18n.t('reader_sources')"
                >{{ i18n.t('reader_sources_short') }}</button>
                <div
                  v-if="showSourceMenu"
                  class="absolute right-0 bottom-full mb-2 w-52 rounded-lg border shadow-lg p-2 z-10"
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
              <div class="relative">
                <button
                  @click="showFontMenu = !showFontMenu"
                  class="reader-icon-btn"
                  :class="(cnFont !== 'default' || enFont !== 'default') ? 'text-accent' : ''"
                  :title="i18n.t('reader_font_settings')"
                >F</button>
                <div
                  v-if="showFontMenu"
                  class="absolute right-0 bottom-full mb-2 w-56 rounded-lg border shadow-lg p-3 z-10"
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
                class="reader-icon-btn"
                :title="isDark ? i18n.t('reader_light_mode') : i18n.t('reader_dark_mode')"
              >{{ isDark ? '\u2600' : '\u263e' }}</button>
            </div>
            <span class="text-xs opacity-70">{{ currentPage + 1 }} / {{ pageCount }}</span>
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
  transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
  will-change: transform;
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

.reader-menu-layer {
  background: transparent;
}

.reader-topbar,
.reader-bottombar {
  position: absolute;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.5rem 0.75rem;
  backdrop-filter: blur(10px);
}

.reader-topbar {
  top: 0;
  border-bottom: 1px solid rgba(128, 128, 128, 0.25);
}

.reader-bottombar {
  bottom: 0;
  justify-content: space-between;
  border-top: 1px solid rgba(128, 128, 128, 0.25);
}

.reader-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 2rem;
  height: 2rem;
  padding: 0 0.4rem;
  border-radius: 0.375rem;
  font-size: 0.8rem;
  transition: background-color 0.15s ease;
}

.reader-icon-btn:hover {
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
</style>
