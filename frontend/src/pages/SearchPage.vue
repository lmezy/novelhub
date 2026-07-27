<script setup lang="ts">
import { ref, watch } from "vue"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"
import { useRouter } from "vue-router"

const router = useRouter()
const query = ref("")
const scope = ref<"books" | "chapters">("books")
const results = ref<any[]>([])
const total = ref(0)
const searching = ref(false)
const searched = ref(false)

let timer: ReturnType<typeof setTimeout>

function doSearch() {
  if (!query.value.trim()) return
  searching.value = true
  searched.value = true
  clearTimeout(timer)
  timer = setTimeout(async () => {
    try {
      const q = encodeURIComponent(query.value.trim())
      const res = await api.get<{ hits: any[]; total: number }>(
        `/search?q=${q}&scope=${scope.value}&limit=30`,
      )
      results.value = res.hits
      total.value = res.total
    } finally {
      searching.value = false
    }
  }, 300)
}

watch(query, () => {
  if (query.value.trim().length >= 2) doSearch()
})
watch(scope, () => {
  if (query.value.trim().length >= 2) doSearch()
})
</script>

<template>
  <div class="min-h-screen bg-paper">
    <NavBar />

    <main class="max-w-3xl mx-auto px-4 py-8">
      <h1 class="text-2xl font-bold mb-6">Search</h1>

      <div class="flex gap-2 mb-6">
        <input
          v-model="query"
          type="search"
          placeholder="Search books, authors, content..."
          class="flex-1 px-4 py-2.5 rounded-lg border border-border bg-surface text-ink placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent/30 text-sm"
          @keydown.enter="doSearch"
        />
        <button
          @click="doSearch"
          class="px-5 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:opacity-90 transition-opacity"
        >Search</button>
      </div>

      <div class="flex gap-3 mb-6">
        <button
          v-for="s in (['books', 'chapters'] as const)"
          :key="s"
          @click="scope = s"
          class="text-xs px-3 py-1 rounded-full transition-colors"
          :class="scope === s ? 'bg-accent text-white' : 'bg-gray-100 text-muted hover:bg-gray-200'"
        >{{ s === 'books' ? 'Books' : 'Chapters' }}</button>
      </div>

      <p v-if="searching" class="text-muted text-sm">Searching...</p>

      <template v-else-if="searched">
        <p class="text-sm text-muted mb-4">{{ total }} results for "{{ query }}"</p>

        <p v-if="results.length === 0" class="text-muted">No results found.</p>

        <div class="divide-y divide-border border border-border rounded-lg bg-surface">
          <div
            v-for="hit in results"
            :key="hit.id"
            class="px-4 py-3 hover:bg-accent/5 cursor-pointer transition-colors"
            @click="
              scope === 'books'
                ? router.push(`/books/${hit.id}`)
                : router.push(`/books/${hit.book_id}/chapters/${hit.id}`)
            "
          >
            <h3 class="text-sm font-medium mb-0.5">{{ hit.title }}</h3>
            <p v-if="hit.author" class="text-xs text-muted">{{ hit.author }}</p>
            <p v-if="hit.content" class="text-xs text-muted mt-1 line-clamp-2">
              {{ hit.content.slice(0, 200) }}
            </p>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>
