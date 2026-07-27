<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useBooksStore, type Chapter, type ChapterContent } from "../stores/books"

const route = useRoute()
const router = useRouter()
const store = useBooksStore()

const chapter = ref<ChapterContent | null>(null)
const chapters = ref<Chapter[]>([])
const loading = ref(true)
const error = ref("")
const fontSize = ref(18)
const isDark = ref(localStorage.getItem("novelhub_dark") === "true")
const showToc = ref(false)

const bookId = computed(() => route.params.bookId as string)
const chapterId = computed(() => route.params.chapterId as string)

const currentIndex = computed(() => chapters.value.findIndex((c) => c.id === chapterId.value))
const prevChapter = computed(() =>
  currentIndex.value > 0 ? chapters.value[currentIndex.value - 1] : null,
)
const nextChapter = computed(() =>
  currentIndex.value < chapters.value.length - 1 ? chapters.value[currentIndex.value + 1] : null,
)

function toggleDark() {
  isDark.value = !isDark.value
  localStorage.setItem("novelhub_dark", String(isDark.value))
  document.documentElement.classList.toggle("dark", isDark.value)
}

async function loadChapter(id: string) {
  loading.value = true
  error.value = ""
  try {
    chapter.value = await store.fetchChapter(id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load chapter"
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  document.documentElement.classList.toggle("dark", isDark.value)
  try {
    chapters.value = await store.fetchChapters(bookId.value)
  } catch {
    // non-fatal
  }
  await loadChapter(chapterId.value)
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
    <!-- Top bar -->
    <header
      class="sticky top-0 z-40 border-b h-12 flex items-center justify-between px-4"
      :class="isDark ? 'bg-gray-900/90 border-gray-800' : 'bg-surface/80 border-border'"
    >
      <div class="flex items-center gap-3">
        <button
          @click="router.push(`/books/${bookId}`)"
          class="text-sm hover:opacity-70 transition-opacity"
        >&larr; TOC</button>
        <span v-if="chapter" class="text-sm truncate max-w-[200px]">
          {{ chapter.title || `Chapter ${chapter.chapter_number}` }}
        </span>
      </div>
      <div class="flex items-center gap-2">
        <button
          @click="fontSize = Math.max(14, fontSize - 2)"
          class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 transition-colors text-sm"
          title="Smaller font"
        >A-</button>
        <button
          @click="fontSize = Math.min(26, fontSize + 2)"
          class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 transition-colors text-sm"
          title="Larger font"
        >A+</button>
        <button
          @click="toggleDark"
          class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 transition-colors text-sm"
          :title="isDark ? 'Light mode' : 'Dark mode'"
        >{{ isDark ? '☀' : '☾' }}</button>
      </div>
    </header>

    <!-- Content -->
    <main class="max-w-3xl mx-auto px-4 py-10">
      <p v-if="loading" class="text-center py-16">Loading...</p>
      <p v-else-if="error" class="text-center py-16 text-red-500">{{ error }}</p>

      <article
        v-else-if="chapter"
        class="reader-content prose"
        :style="{ fontSize: fontSize + 'px' }"
      >
        <h1 class="text-2xl font-bold mb-8 text-center">
          {{ chapter.title || `Chapter ${chapter.chapter_number}` }}
        </h1>
        <div v-html="chapter.content.replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>')" />
      </article>

      <!-- Navigation -->
      <nav
        v-if="chapter"
        class="flex items-center justify-between mt-12 pt-6 border-t"
        :class="isDark ? 'border-gray-800' : 'border-border'"
      >
        <router-link
          v-if="prevChapter"
          :to="`/books/${bookId}/chapters/${prevChapter.id}`"
          class="text-sm hover:opacity-70 transition-opacity no-underline"
        >&larr; {{ prevChapter.title || `Ch. ${prevChapter.chapter_number}` }}</router-link>
        <span v-else class="text-sm text-muted">Start</span>

        <router-link
          v-if="nextChapter"
          :to="`/books/${bookId}/chapters/${nextChapter.id}`"
          class="text-sm hover:opacity-70 transition-opacity no-underline"
        >{{ nextChapter.title || `Ch. ${nextChapter.chapter_number}` }} &rarr;</router-link>
        <span v-else class="text-sm text-muted">End</span>
      </nav>
    </main>

    <!-- TOC sidebar overlay -->
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
            <h3 class="font-semibold text-sm">Table of Contents</h3>
            <button @click="showToc = false" class="text-muted text-lg">&times;</button>
          </div>
          <router-link
            v-for="ch in chapters"
            :key="ch.id"
            :to="`/books/${bookId}/chapters/${ch.id}`"
            @click="showToc = false"
            class="block py-1.5 text-sm no-underline truncate"
            :class="
              ch.id === chapterId
                ? 'text-accent font-medium'
                : isDark ? 'text-gray-400 hover:text-gray-200' : 'text-muted hover:text-ink'
            "
          >{{ ch.chapter_number }}. {{ ch.title || `Chapter ${ch.chapter_number}` }}</router-link>
        </div>
        <div class="flex-1" @click="showToc = false" />
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
