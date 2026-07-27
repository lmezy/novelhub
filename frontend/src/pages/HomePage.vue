<script setup lang="ts">
import { onMounted } from "vue"
import { useBooksStore } from "../stores/books"
import NavBar from "../components/NavBar.vue"

const store = useBooksStore()

onMounted(() => {
  store.fetchBooks()
})
</script>

<template>
  <div class="min-h-screen bg-paper">
    <NavBar />

    <main class="max-w-5xl mx-auto px-4 py-8">
      <section class="mb-10">
        <div class="flex items-center justify-between mb-6">
          <h1 class="text-2xl font-bold tracking-tight">Library</h1>
          <span class="text-sm text-muted">{{ store.books.length }} books</span>
        </div>

        <p v-if="store.loading" class="text-muted">Loading...</p>
        <p v-else-if="store.error" class="text-red-600">{{ store.error }}</p>

        <div v-else-if="store.books.length === 0" class="text-center py-16">
          <p class="text-muted text-lg mb-2">Your library is empty</p>
          <p class="text-sm text-muted">
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
            :to="`/books/${book.id}`"
            class="block p-5 rounded-lg border border-border bg-surface hover:shadow-md hover:border-accent/30 transition-all duration-200 no-underline"
          >
            <h3 class="font-semibold text-ink mb-1 truncate">{{ book.title }}</h3>
            <p class="text-sm text-muted line-clamp-2 mb-3">
              {{ book.description || "No description" }}
            </p>
            <div class="flex items-center gap-2">
              <span
                class="text-xs px-2 py-0.5 rounded-full"
                :class="book.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'"
              >{{ book.status || "unknown" }}</span>
              <span class="text-xs text-muted ml-auto">
                {{ new Date(book.updated_at).toLocaleDateString() }}
              </span>
            </div>
          </router-link>
        </div>
      </section>
    </main>
  </div>
</template>
