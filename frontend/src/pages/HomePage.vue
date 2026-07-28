<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useBooksStore, type Book } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"

const store = useBooksStore()
const auth = useAuthStore()
const recentReads = ref<Book[]>([])

async function deleteBook(id: string, title: string) {
  if (!confirm('Delete "' + title + '"? This cannot be undone.')) return
  try {
    await api.delete('/books/' + id)
    await store.fetchBooks()
  } catch (e) {
    alert(e instanceof Error ? e.message : "Delete failed")
  }
}

onMounted(async () => {
  await store.fetchBooks()
  if (auth.user) {
    try {
      const progress = await api.get<any[]>('/progress?user_id=' + auth.user.id)
      if (progress.length > 0) {
        const results = await Promise.all(
          progress.map((p: any) => store.fetchBook(p.book_id).catch(() => null))
        )
        recentReads.value = results.filter(Boolean) as Book[]
      }
    } catch { /* non-critical */ }
  }
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-800 dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-5xl mx-auto px-4 py-8">
      <!-- Recent Reads -->
      <section v-if="recentReads.length > 0" class="mb-10">
        <h2 class="text-lg font-semibold mb-3">Continue Reading</h2>
        <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <router-link
            v-for="b in recentReads.slice(0, 4)"
            :key="b.id"
            :to="'/books/' + b.id"
            class="p-3 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 hover:shadow-sm hover:border-accent/30 transition-all no-underline"
          >
            <p class="text-sm font-medium truncate">{{ b.title }}</p>
            <p class="text-xs text-muted dark:text-gray-400 mt-1">{{ b.status || 'Reading' }}</p>
          </router-link>
        </div>
      </section>

      <!-- Library -->
      <section>
        <div class="flex items-center justify-between mb-6">
          <h1 class="text-2xl font-bold tracking-tight">Library</h1>
          <span class="text-sm text-muted dark:text-gray-400">{{ store.books.length }} books</span>
        </div>

        <p v-if="store.loading" class="text-muted dark:text-gray-400">Loading...</p>
        <p v-else-if="store.error" class="text-red-600">{{ store.error }}</p>

        <div v-else-if="store.books.length === 0" class="text-center py-16">
          <p class="text-muted dark:text-gray-400 text-lg mb-2">Your library is empty</p>
          <p class="text-sm text-muted dark:text-gray-400">
            Create a source and trigger a sync to import your first book.
          </p>
          <router-link
            to="/admin"
            class="inline-block mt-4 text-sm text-accent hover:underline"
          >Go to Admin</router-link>
        </div>

        <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <router-link
            v-for="book in store.books"
            :key="book.id"
            :to="'/books/' + book.id"
            class="relative group block p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 hover:shadow-md hover:border-accent/30 transition-all duration-200 no-underline"
          >
            <button
              v-if="auth.isAdmin"
              @click.prevent.stop="deleteBook(book.id, book.title)"
              class="absolute top-2 right-2 text-xs text-red-400 hover:text-red-600 opacity-0 group-hover:opacity-100 transition-opacity"
              title="Delete book"
            >&times;</button>
            <h3 class="font-semibold text-ink mb-1 truncate">{{ book.title }}</h3>
            <p v-if="book.author_name" class="text-xs text-muted dark:text-gray-400 mb-1">{{ book.author_name }}</p>
            <div v-if="book.tag_names?.length" class="flex flex-wrap gap-1 mb-2">
              <span
                v-for="tag in book.tag_names"
                :key="tag"
                class="text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-muted dark:text-gray-400"
              >{{ tag }}</span>
            </div>
            <p class="text-sm text-muted dark:text-gray-400 line-clamp-2 mb-3">
              {{ book.description || "No description" }}
            </p>
            <div class="flex items-center gap-2">
              <span
                class="text-xs px-2 py-0.5 rounded-full"
                :class="book.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'"
              >{{ book.status || "unknown" }}</span>
              <span class="text-xs text-muted dark:text-gray-400 ml-auto">
                {{ new Date(book.updated_at).toLocaleDateString() }}
              </span>
            </div>
          </router-link>
        </div>
      </section>
    </main>
  </div>
</template>
