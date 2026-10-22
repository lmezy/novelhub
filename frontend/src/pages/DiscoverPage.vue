<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { useRoute } from "vue-router"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"
import { useI18nStore } from "../stores/i18n"

interface Hit {
  book_id: string
  title: string
  author?: string
  cover?: string | null
  is_r18?: boolean
  similarity: number
  excerpt?: string
}

const i18n = useI18nStore()
const route = useRoute()

const query = ref("")
const loading = ref(false)
const error = ref("")
const hits = ref<Hit[]>([])
const mode = ref<"text" | "book">("text")

async function search(payload: { query?: string; book_id?: string }) {
  loading.value = true
  error.value = ""
  hits.value = []
  try {
    const res = await api.post<any>("/rag/library/search", {
      query: payload.query || "",
      book_id: payload.book_id || null,
      top_k: 12,
    })
    hits.value = Array.isArray(res.items) ? res.items : []
    if (!hits.value.length) error.value = i18n.t("discover_empty")
  } catch (e) {
    error.value = e instanceof Error ? e.message : i18n.t("discover_failed")
  } finally {
    loading.value = false
  }
}

function runTextSearch() {
  const text = query.value.trim()
  if (!text) return
  mode.value = "text"
  void search({ query: text })
}

function scoreLabel(similarity: number): string {
  const value = Math.round(Math.max(0, Math.min(1, Number(similarity) || 0)) * 100)
  return `${value}%`
}

const canSearch = computed(() => !!query.value.trim() && !loading.value)

onMounted(() => {
  const bookId = String(route.query.book_id || "")
  if (bookId) {
    mode.value = "book"
    void search({ book_id: bookId })
  }
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-5xl mx-auto px-4 py-8">
      <h1 class="text-xl font-semibold mb-1">{{ i18n.t('discover_title') }}</h1>
      <p class="text-xs text-muted dark:text-gray-400 mb-5">{{ i18n.t('discover_hint') }}</p>

      <section class="p-4 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 mb-5">
        <textarea
          v-model="query"
          rows="4"
          :placeholder="i18n.t('discover_placeholder')"
          class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 resize-y"
        ></textarea>
        <div class="flex flex-wrap items-center gap-3 mt-3">
          <button
            @click="runTextSearch"
            :disabled="!canSearch"
            class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >{{ loading ? i18n.t('discover_searching') : i18n.t('discover_search') }}</button>
          <span v-if="mode === 'book'" class="text-xs text-muted dark:text-gray-400">
            {{ i18n.t('discover_from_book') }}
          </span>
        </div>
      </section>

      <p v-if="error" class="text-sm text-red-500 mb-4 break-words">{{ error }}</p>

      <div v-if="hits.length" class="space-y-3">
        <router-link
          v-for="hit in hits"
          :key="hit.book_id"
          :to="'/books/' + hit.book_id"
          class="flex gap-3 p-3 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 no-underline hover:border-accent/40 transition-colors"
        >
          <img
            v-if="hit.cover"
            :src="hit.cover"
            :alt="hit.title"
            class="w-16 h-24 object-cover rounded shrink-0"
            loading="lazy"
          />
          <div class="min-w-0 flex-1">
            <div class="flex items-baseline gap-2 flex-wrap">
              <span class="font-medium text-ink truncate">{{ hit.title }}</span>
              <span class="text-[11px] px-1.5 py-0.5 rounded bg-accent/10 text-accent shrink-0">{{ scoreLabel(hit.similarity) }}</span>
              <span v-if="hit.is_r18" class="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-500 shrink-0">R18</span>
            </div>
            <p v-if="hit.author" class="text-xs text-muted dark:text-gray-400 mt-0.5">{{ hit.author }}</p>
            <p v-if="hit.excerpt" class="text-xs text-muted dark:text-gray-400 mt-2 max-h-20 overflow-hidden whitespace-pre-wrap">{{ hit.excerpt }}</p>
          </div>
        </router-link>
      </div>
    </main>
  </div>
</template>
