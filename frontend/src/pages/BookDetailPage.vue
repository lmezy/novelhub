<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useBooksStore, type Book, type Chapter } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"

const route = useRoute()
const router = useRouter()
const store = useBooksStore()
const auth = useAuthStore()

const book = ref<Book | null>(null)
const chapters = ref<Chapter[]>([])
const loading = ref(true)
const error = ref("")
const savedChapterId = ref<string | null>(null)
const syncing = ref(false)

async function deleteThisBook() {
  if (!book.value || !confirm('Delete "' + book.value.title + '"? This cannot be undone.')) return
  try {
    await api.delete('/books/' + book.value.id)
    router.push("/")
  } catch (e) {
    alert(e instanceof Error ? e.message : "Delete failed")
  }
}

async function resyncBook() {
  if (!book.value) return
  syncing.value = true
  try {
    const result = await api.post('/books/' + book.value.id + '/sync')
    alert('Synced: ' + result.created_chapters + ' new chapters, ' + result.skipped_chapters + ' skipped')
    chapters.value = await store.fetchChapters(book.value.id)
  } catch (e) {
    alert(e instanceof Error ? e.message : "Sync failed")
  } finally {
    syncing.value = false
  }
}

onMounted(async () => {
  try {
    book.value = await store.fetchBook(route.params.id as string)
    chapters.value = await store.fetchChapters(route.params.id as string)
    if (auth.user) {
      try {
        const progress = await api.get<any[]>('/progress?user_id=' + auth.user.id)
        const p = progress.find((p: any) => p.book_id === route.params.id)
        if (p) savedChapterId.value = p.chapter_id
      } catch { /* non-critical */ }
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load book"
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
      >&larr; Back</button>

      <p v-if="loading" class="text-muted dark:text-gray-400">Loading...</p>
      <p v-else-if="error" class="text-red-600">{{ error }}</p>

      <template v-else-if="book">
        <router-link
          v-if="savedChapterId"
          :to="'/books/' + book.id + '/chapters/' + savedChapterId"
          class="inline-flex items-center gap-1 px-4 py-2 mb-6 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors no-underline"
        >Continue Reading &rarr;</router-link>

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
            >{{ book.status || "unknown" }}</span>
            <span class="text-xs text-muted dark:text-gray-400">
              Updated {{ new Date(book.updated_at).toLocaleDateString() }}
            </span>
          </div>
          <div class="flex items-center gap-2 mt-4">
            <button
              v-if="auth.isAdmin"
              @click="deleteThisBook"
              class="px-3 py-1 text-xs text-red-500 border border-red-200 rounded hover:bg-red-50 transition-colors"
            >Delete Book</button>
            <a
              :href="'/api/books/' + book.id + '/epub'"
              class="inline-flex items-center gap-1 px-4 py-2 rounded-lg border border-border dark:border-gray-700 text-sm hover:bg-accent/5 transition-colors no-underline"
              download
            >Download EPUB</a>
            <button
              v-if="auth.isAdmin && book.source_id"
              @click="resyncBook"
              :disabled="syncing"
              class="px-3 py-1 text-xs border border-border dark:border-gray-700 rounded hover:bg-accent/5 transition-colors disabled:opacity-50"
            >{{ syncing ? 'Syncing...' : 'Re-sync' }}</button>
          </div>
        </header>

        <section>
          <h2 class="text-lg font-semibold mb-3">
            Chapters
            <span class="text-sm font-normal text-muted dark:text-gray-400">({{ chapters.length }})</span>
          </h2>

          <p v-if="chapters.length === 0" class="text-muted dark:text-gray-400 text-sm">No chapters yet.</p>

          <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
            <router-link
              v-for="ch in chapters"
              :key="ch.id"
              :to="'/books/' + book.id + '/chapters/' + ch.id"
              class="flex items-center justify-between px-4 py-3 hover:bg-accent/5 transition-colors no-underline"
            >
              <span class="text-sm">
                <span class="text-muted dark:text-gray-400 mr-2">{{ ch.chapter_number }}.</span>
                {{ ch.title || 'Chapter ' + ch.chapter_number }}
              </span>
              <span class="text-xs text-muted dark:text-gray-400">&rarr;</span>
            </router-link>
          </div>
        </section>
      </template>
    </main>
  </div>
</template>
