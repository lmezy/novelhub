<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
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

const cnFonts = [
  { value: "default", label: "\u7cfb\u7edf\u9ed8\u8ba4" },
  { value: "song", label: "\u5b8b\u4f53" },
  { value: "kai", label: "\u6977\u4f53" },
  { value: "hei", label: "\u9ed1\u4f53" },
  { value: "fang", label: "\u4eff\u5b8b" },
]
const enFonts = [
  { value: "default", label: "System" },
  { value: "serif", label: "Serif" },
  { value: "sans", label: "Sans" },
  { value: "mono", label: "Mono" },
]

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

let scrollTimer: ReturnType<typeof setTimeout>

function onScroll() {
  clearTimeout(scrollTimer)
  scrollTimer = setTimeout(() => {
    if (!auth.user) return
    const scrollPercent = Math.round(
      (window.scrollY / (document.documentElement.scrollHeight - window.innerHeight)) * 100
    )
    api.put("/progress", {
      user_id: auth.user.id,
      book_id: bookId.value,
      chapter_id: chapterId.value,
      position: scrollPercent,
    }).catch(() => {})
  }, 2000)
}

function toggleDark() {
  isDark.value = !isDark.value
  localStorage.setItem("novelhub_dark", String(isDark.value))
  document.documentElement.classList.toggle("dark", isDark.value)
}

function setCnFont(f: string) {
  cnFont.value = f
  localStorage.setItem("novelhub_cn_font", f)
}
function setEnFont(f: string) {
  enFont.value = f
  localStorage.setItem("novelhub_en_font", f)
}

async function saveProgress() {
  if (!auth.user || !chapter.value) return
  try {
    await api.put("/progress", {
      user_id: auth.user.id,
      book_id: bookId.value,
      chapter_id: chapterId.value,
      position: currentIndex.value,
    })
  } catch { /* non-critical */ }
}

async function loadChapter(id: string) {
  loading.value = true
  error.value = ""
  try {
    chapter.value = await store.fetchChapter(id)
    await saveProgress()
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load chapter"
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  document.documentElement.classList.toggle("dark", isDark.value)
  window.addEventListener("scroll", onScroll, { passive: true })
  try {
    chapters.value = await store.fetchChapters(bookId.value)
  } catch { /* non-fatal */ }
  await loadChapter(chapterId.value)
})

onUnmounted(() => {
  window.removeEventListener("scroll", onScroll)
  clearTimeout(scrollTimer)
})

watch(
  () => route.params.chapterId,
  async (newId) => {
    if (newId) await loadChapter(newId as string)
  },
)
</script>

<template>
  <div class="min-h-screen" :class="isDark ? 'bg-gray-950 text-gray-100' : 'bg-paper text-ink'">
    <header
      class="sticky top-0 z-40 border-b h-12 flex items-center justify-between px-4"
      :class="isDark ? 'bg-gray-900/90 border-gray-800' : 'bg-surface/80 border-border'"
    >
      <div class="flex items-center gap-3">
        <button
          @click="router.push('/books/' + bookId)"
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
          {{ chapter.title || 'Chapter ' + chapter.chapter_number }}
        </span>
      </div>
      <div class="flex items-center gap-2">
        <button
          @click="fontSize = Math.max(14, fontSize - 2)"
          class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-sm"
          :title="i18n.t('reader_smaller_font')"
        >A-</button>
        <button
          @click="fontSize = Math.min(26, fontSize + 2)"
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
          {{ chapter.title || 'Chapter ' + chapter.chapter_number }}
        </h1>
        <div v-html="chapter.content.replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>')" />
      </article>

      <nav
        v-if="chapter"
        class="flex items-center justify-between mt-12 pt-6 border-t"
        :class="isDark ? 'border-gray-800' : 'border-border'"
      >
        <router-link
          v-if="prevChapter"
          :to="'/books/' + bookId + '/chapters/' + prevChapter.id"
          class="text-sm hover:opacity-70 transition-opacity no-underline"
        >&larr; {{ prevChapter.title || 'Ch. ' + prevChapter.chapter_number }}</router-link>
        <span v-else class="text-sm text-muted">{{ i18n.t('reader_start') }}</span>
        <router-link
          v-if="nextChapter"
          :to="'/books/' + bookId + '/chapters/' + nextChapter.id"
          class="text-sm hover:opacity-70 transition-opacity no-underline"
        >{{ nextChapter.title || 'Ch. ' + nextChapter.chapter_number }} &rarr;</router-link>
        <span v-else class="text-sm text-muted">{{ i18n.t('reader_end') }}</span>
      </nav>
    </main>

    <Teleport to="body">
      <div
        v-if="showToc"
        class="fixed inset-0 z-50 flex"
        @click.self="showToc = false"
      >
        <div
          class="w-72 h-full overflow-y-auto shadow-xl p-4"
          :class="isDark ? 'bg-gray-900' : 'bg-surface'"
        >
          <div class="flex items-center justify-between mb-4">
            <h3 class="font-semibold text-sm">{{ i18n.t('reader_toc_title') }}</h3>
            <button @click="showToc = false" class="text-muted text-lg">&times;</button>
          </div>
          <router-link
            v-for="ch in chapters"
            :key="ch.id"
            :to="'/books/' + bookId + '/chapters/' + ch.id"
            @click="showToc = false"
            class="block py-1.5 text-sm no-underline truncate"
            :class="
              ch.id === chapterId
                ? 'text-accent font-medium'
                : isDark ? 'text-gray-400 hover:text-gray-200' : 'text-muted hover:text-ink'
            "
          >{{ ch.chapter_number }}. {{ ch.title || 'Chapter ' + ch.chapter_number }}</router-link>
        </div>
        <div class="flex-1" @click="showToc = false" />
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="showAI"
        class="fixed inset-0 z-50 flex justify-end"
        @click.self="showAI = false"
      >
        <div
          class="w-80 h-full shadow-xl flex flex-col"
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
</style>