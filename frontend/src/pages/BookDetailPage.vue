<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useBooksStore, type Book, type Chapter } from "../stores/books"
import NavBar from "../components/NavBar.vue"

const route = useRoute()
const router = useRouter()
const store = useBooksStore()

const book = ref<Book | null>(null)
const chapters = ref<Chapter[]>([])
const loading = ref(true)
const error = ref("")

onMounted(async () => {
  try {
    book.value = await store.fetchBook(route.params.id as string)
    chapters.value = await store.fetchChapters(route.params.id as string)
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load book"
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="min-h-screen bg-paper">
    <NavBar />

    <main class="max-w-3xl mx-auto px-4 py-8">
      <button
        @click="router.back()"
        class="text-sm text-muted hover:text-ink mb-6 inline-flex items-center gap-1 transition-colors"
      >&larr; Back</button>

      <p v-if="loading" class="text-muted">Loading...</p>
      <p v-else-if="error" class="text-red-600">{{ error }}</p>

      <template v-else-if="book">
        <header class="mb-8">
          <h1 class="text-3xl font-bold mb-2">{{ book.title }}</h1>
          <p v-if="book.description" class="text-muted mb-3">{{ book.description }}</p>
          <div class="flex items-center gap-3">
            <span
              class="text-xs px-2 py-0.5 rounded-full"
              :class="book.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'"
            >{{ book.status || "unknown" }}</span>
            <span class="text-xs text-muted">
              Updated {{ new Date(book.updated_at).toLocaleDateString() }}
            </span>
          </div>
        </header>

        <section>
          <h2 class="text-lg font-semibold mb-3">
            Chapters
            <span class="text-sm font-normal text-muted">({{ chapters.length }})</span>
          </h2>

          <p v-if="chapters.length === 0" class="text-muted text-sm">No chapters yet.</p>

          <div class="divide-y divide-border border border-border rounded-lg bg-surface">
            <router-link
              v-for="ch in chapters"
              :key="ch.id"
              :to="`/books/${book.id}/chapters/${ch.id}`"
              class="flex items-center justify-between px-4 py-3 hover:bg-accent/5 transition-colors no-underline"
            >
              <span class="text-sm">
                <span class="text-muted mr-2">{{ ch.chapter_number }}.</span>
                {{ ch.title || `Chapter ${ch.chapter_number}` }}
              </span>
              <span class="text-xs text-muted">&rarr;</span>
            </router-link>
          </div>
        </section>
      </template>
    </main>
  </div>
</template>
