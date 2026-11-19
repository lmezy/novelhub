<script setup lang="ts">
import { computed, ref, watch } from "vue"
import { useRoute } from "vue-router"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"
import { useAuthStore } from "../stores/auth"
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
const auth = useAuthStore()

const query = ref("")
const loading = ref(false)
const error = ref("")
const hits = ref<Hit[]>([])
const mode = ref<"text" | "book">("text")
const searched = ref(false)
let latestSearch = 0
const showCovers = computed(() => auth.user?.settings?.show_covers !== false)

async function search(payload: { query?: string; book_id?: string }) {
  const searchId = ++latestSearch
  loading.value = true
  searched.value = true
  error.value = ""
  hits.value = []
  try {
    const res = await api.post<any>("/rag/library/search", {
      query: payload.query || "",
      book_id: payload.book_id || null,
      top_k: 12,
    })
    if (searchId === latestSearch) hits.value = Array.isArray(res.items) ? res.items : []
  } catch (e) {
    if (searchId === latestSearch) error.value = e instanceof Error ? e.message : i18n.t("discover_failed")
  } finally {
    if (searchId === latestSearch) loading.value = false
  }
}

function runTextSearch() {
  const text = query.value.trim()
  if (!text || loading.value) return
  mode.value = "text"
  void search({ query: text })
}

function scoreLabel(similarity: number): string {
  const value = Math.round(Math.max(0, Math.min(1, Number(similarity) || 0)) * 100)
  return `${value}%`
}

const canSearch = computed(() => !!query.value.trim() && !loading.value)

watch(() => route.query.book_id, (bookId) => {
  if (typeof bookId === "string" && bookId.trim()) {
    mode.value = "book"
    void search({ book_id: bookId })
  } else if (mode.value === "book") {
    latestSearch += 1
    mode.value = "text"
    searched.value = false
    loading.value = false
    hits.value = []
    error.value = ""
  }
}, { immediate: true })
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="mx-auto max-w-5xl px-4 pb-16 pt-7 sm:px-6 sm:pt-10">
      <header class="mb-5 sm:mb-7">
        <h1 class="text-3xl font-semibold tracking-tight sm:text-4xl">{{ i18n.t('discover_title') }}</h1>
        <p class="mt-2 max-w-lg text-sm leading-relaxed text-muted dark:text-gray-400">{{ i18n.t('discover_hint') }}</p>
      </header>

      <form class="rounded-2xl border border-border bg-surface p-4 dark:border-gray-800 dark:bg-gray-900 sm:p-6" @submit.prevent="runTextSearch">
        <label for="discover-query" class="mb-2 block text-sm font-medium">{{ i18n.t('discover_query_label') }}</label>
        <textarea
          id="discover-query"
          v-model="query"
          rows="4"
          :placeholder="i18n.t('discover_placeholder')"
          class="w-full resize-y rounded-xl border border-border bg-paper px-3 py-3 text-base leading-relaxed dark:border-gray-700 dark:bg-gray-950 sm:text-sm"
        ></textarea>
        <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            type="submit"
            :disabled="!canSearch"
            class="min-h-11 rounded-lg bg-accent px-5 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >{{ loading ? i18n.t('discover_searching') : i18n.t('discover_search') }}</button>
          <router-link v-if="auth.isAdmin" :to="{ name: 'settings', query: { tab: 'ai' } }" class="inline-flex min-h-10 items-center text-sm font-medium text-accent hover:underline">
            {{ i18n.t('discover_index_link') }}
          </router-link>
        </div>
      </form>

      <p v-if="mode === 'book'" class="mt-4 text-sm text-muted dark:text-gray-400">{{ i18n.t('discover_from_book') }}</p>
      <p v-if="error" role="alert" class="mt-5 break-words rounded-xl border border-red-200 bg-surface p-4 text-sm text-red-700 dark:border-red-900 dark:bg-gray-900 dark:text-red-300">{{ error }}</p>
      <p v-else-if="searched && !loading && !hits.length" role="status" class="mt-5 rounded-xl border border-border bg-surface p-4 text-sm text-muted dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400">{{ i18n.t('discover_empty') }}</p>

      <section v-if="hits.length" class="mt-6" aria-labelledby="discover-results">
        <h2 id="discover-results" class="mb-3 text-lg font-semibold">{{ i18n.t('discover_results', { count: hits.length }) }}</h2>
        <ul class="grid gap-3 sm:grid-cols-2">
          <li v-for="hit in hits" :key="hit.book_id" class="min-w-0">
            <router-link
              :to="{ name: 'book-detail', params: { id: hit.book_id } }"
              class="flex h-full min-w-0 gap-3 rounded-xl border border-border bg-surface p-3.5 no-underline transition-colors hover:border-accent/50 dark:border-gray-800 dark:bg-gray-900 sm:gap-4 sm:p-4"
            >
              <img
                v-if="showCovers && hit.cover"
                :src="hit.cover"
                alt=""
                class="aspect-[2/3] w-[4.5rem] shrink-0 self-start rounded-md bg-paper object-cover dark:bg-gray-800"
                loading="lazy"
                decoding="async"
              />
              <div class="min-w-0 flex-1">
                <h3 class="break-words text-base font-semibold leading-snug">{{ hit.title }}</h3>
                <p v-if="hit.author" class="mt-1 truncate text-xs text-muted dark:text-gray-400">{{ hit.author }}</p>
                <p v-if="hit.excerpt" class="mt-2 line-clamp-3 whitespace-pre-line break-words text-sm leading-relaxed text-muted dark:text-gray-400">{{ hit.excerpt }}</p>
                <div class="mt-3 flex flex-wrap gap-1.5">
                  <span class="rounded-md bg-accent/10 px-2 py-1 text-xs font-medium tabular-nums text-accent">{{ i18n.t('discover_similarity', { score: scoreLabel(hit.similarity) }) }}</span>
                  <span v-if="hit.is_r18" class="rounded-md bg-red-500/10 px-2 py-1 text-xs text-red-700 dark:text-red-300">R18</span>
                </div>
              </div>
            </router-link>
          </li>
        </ul>
      </section>
    </main>
  </div>
</template>
