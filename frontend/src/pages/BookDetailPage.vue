<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useBooksStore, type Book, type Chapter } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"

const route = useRoute()
const router = useRouter()
const store = useBooksStore()
const auth = useAuthStore()
const i18n = useI18nStore()

const book = ref<Book | null>(null)
const chapters = ref<Chapter[]>([])
const loading = ref(true)
const error = ref("")
const savedChapterId = ref<string | null>(null)
const syncing = ref(false)
const favorite = ref(false)
const alternates = ref<any[]>([])
const showSources = ref(false)

async function toggleFavorite() {
  if (!book.value) return
  favorite.value = await store.toggleFavorite(book.value)
}

async function deleteThisBook() {
  if (!book.value || !confirm(i18n.t('home_delete_confirm', { title: book.value.title }))) return
  try {
    await api.delete('/books/' + book.value.id)
    router.push("/")
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('book_delete_failed'))
  }
}

async function resyncBook() {
  if (!book.value) return
  syncing.value = true
  try {
    const result = await api.post('/books/' + book.value.id + '/sync')
    const failed = result.failed_chapters?.length || 0
    const failedSuffix = failed ? i18n.t('book_failed_suffix', { n: failed }) : ''
    alert(i18n.t('book_sync_result', {
      created: result.created_chapters,
      skipped: result.skipped_chapters,
      failed: failedSuffix,
    }))
    chapters.value = await store.fetchChapters(book.value.id)
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('book_sync_failed'))
  } finally {
    syncing.value = false
  }
}

async function loadAlternates() {
  try {
    const res = await api.get<any>("/books/" + route.params.id + "/sources")
    alternates.value = res.sources || []
  } catch { /* non-critical */ }
}

function switchSource(id: string) {
  router.push("/books/" + id)
}

onMounted(async () => {
  try {
    book.value = await store.fetchBook(route.params.id as string)
    favorite.value = book.value.is_favorite || false
    chapters.value = await store.fetchChapters(route.params.id as string)
    await loadAlternates()
    if (auth.user) {
      try {
        const progress = await api.get<any[]>('/progress?user_id=' + auth.user.id)
        const p = progress.find((p: any) => p.book_id === route.params.id)
        if (p) savedChapterId.value = p.chapter_id
      } catch { /* non-critical */ }
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : i18n.t('book_load_failed')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-800 dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-3xl mx-auto px-4 py-8">
      <button
        @click="router.back()"
        class="text-sm text-muted dark:text-gray-400 hover:text-ink mb-6 inline-flex items-center gap-1 transition-colors"
      >&larr; {{ i18n.t('book_back') }}</button>

      <p v-if="loading" class="text-muted dark:text-gray-400">{{ i18n.t('book_loading') }}</p>
      <p v-else-if="error" class="text-red-600">{{ error }}</p>

      <template v-else-if="book">
        <router-link
          v-if="savedChapterId"
          :to="'/books/' + book.id + '/chapters/' + savedChapterId"
          class="inline-flex items-center gap-1 px-4 py-2 mb-6 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors no-underline"
        >{{ i18n.t('book_continue') }} &rarr;</router-link>

        <header class="mb-8">
          <h1 class="text-3xl font-bold mb-2">{{ book.title }}</h1>
          <p v-if="book.author_name" class="text-muted dark:text-gray-400 mb-1">{{ book.author_name }}</p>
          <div v-if="book.tag_names?.length" class="flex flex-wrap gap-1 mb-2">
            <span
              v-for="tag in book.tag_names"
              :key="tag"
              class="text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-muted dark:text-gray-400"
            >{{ tag }}</span>
          </div>
          <p v-if="book.description" class="text-muted dark:text-gray-400 mb-3">{{ book.description }}</p>
          <div class="flex items-center gap-3">
            <span
              class="text-xs px-2 py-0.5 rounded-full"
              :class="book.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'"
            >{{ book.status || i18n.t('book_unknown_status') }}</span>
            <span class="text-xs text-muted dark:text-gray-400">
              {{ i18n.t('book_updated', { date: new Date(book.updated_at).toLocaleDateString() }) }}
            </span>
          </div>
          <div v-if="alternates.length > 1" class="mt-4">
            <button
              @click="showSources = !showSources"
              class="px-3 py-1 text-xs border border-border dark:border-gray-700 rounded hover:bg-accent/5 transition-colors"
            >{{ i18n.t('book_sources') }} ({{ alternates.length }})</button>
            <div
              v-if="showSources"
              class="mt-2 divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900"
            >
              <button
                v-for="alt in alternates"
                :key="alt.id"
                @click="switchSource(alt.id)"
                class="w-full flex items-center justify-between px-3 py-2 text-left text-sm hover:bg-accent/5 transition-colors"
                :class="alt.is_current ? 'text-accent font-medium' : ''"
              >
                <span>
                  {{ alt.source_name || alt.source_id || i18n.t('book_unknown_source') }}
                  <span class="text-xs text-muted dark:text-gray-400">
                    ({{ i18n.t('book_chapters_count', { n: alt.chapter_count }) }})
                  </span>
                </span>
                <span class="text-xs text-muted dark:text-gray-400">
                  {{ alt.is_current ? i18n.t('book_current') : i18n.t('book_switch') }}
                </span>
              </button>
            </div>
          </div>
          <div class="flex items-center gap-2 mt-4">
            <button
              @click="toggleFavorite"
              class="px-3 py-1 text-xs border border-border dark:border-gray-700 rounded hover:bg-accent/5 transition-colors"
            >{{ favorite ? i18n.t('book_favorite_added') : i18n.t('book_favorite') }}</button>
            <button
              v-if="auth.isAdmin"
              @click="deleteThisBook"
              class="px-3 py-1 text-xs text-red-500 border border-red-200 rounded hover:bg-red-50 transition-colors"
            >{{ i18n.t('book_delete') }}</button>
            <a
              :href="'/api/books/' + book.id + '/epub'"
              class="inline-flex items-center gap-1 px-4 py-2 rounded-lg border border-border dark:border-gray-700 text-sm hover:bg-accent/5 transition-colors no-underline"
              download
            >{{ i18n.t('book_download_epub') }}</a>
            <button
              v-if="auth.isAdmin && book.source_id"
              @click="resyncBook"
              :disabled="syncing"
              class="px-3 py-1 text-xs border border-border dark:border-gray-700 rounded hover:bg-accent/5 transition-colors disabled:opacity-50"
            >{{ syncing ? i18n.t('book_syncing') : i18n.t('book_resync') }}</button>
          </div>
        </header>

        <section>
          <h2 class="text-lg font-semibold mb-3">
            {{ i18n.t('book_chapters') }}
            <span class="text-sm font-normal text-muted dark:text-gray-400">({{ chapters.length }})</span>
          </h2>

          <p v-if="chapters.length === 0" class="text-muted dark:text-gray-400 text-sm">{{ i18n.t('book_no_chapters') }}</p>

          <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
            <router-link
              v-for="ch in chapters"
              :key="ch.id"
              :to="'/books/' + book.id + '/chapters/' + ch.id"
              class="flex items-center justify-between px-4 py-3 hover:bg-accent/5 transition-colors no-underline"
            >
              <span class="text-sm">
                <span class="text-muted dark:text-gray-400 mr-2">{{ ch.chapter_number }}.</span>
                {{ ch.title || i18n.t('book_chapter', { n: ch.chapter_number }) }}
              </span>
              <span class="text-xs text-muted dark:text-gray-400">&rarr;</span>
            </router-link>
          </div>
        </section>
      </template>
    </main>
  </div>
</template>
