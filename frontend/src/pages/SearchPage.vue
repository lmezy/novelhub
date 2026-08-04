<script setup lang="ts">
import { onMounted, ref, watch } from "vue"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"
import { useRouter } from "vue-router"
import { useAuthStore } from "../stores/auth"

type SearchMode = "library" | "sources"

const router = useRouter()
const auth = useAuthStore()
const mode = ref<SearchMode>("library")
const query = ref("")
const scope = ref<"books" | "chapters">("books")
const sources = ref<{ id: string; name: string }[]>([])
const sourceId = ref("")
const results = ref<any[]>([])
const remoteResults = ref<any[]>([])
const total = ref(0)
const remoteTotal = ref(0)
const searching = ref(false)
const searched = ref(false)
const error = ref("")
const syncingUrl = ref("")

let timer: ReturnType<typeof setTimeout>

async function loadSources() {
  try {
    sources.value = await api.get<{ id: string; name: string }[]>("/sources")
    if (!sourceId.value && sources.value.length) {
      sourceId.value = sources.value[0].id
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load sources"
  }
}

function doSearch() {
  if (!query.value.trim()) return
  searching.value = true
  searched.value = true
  error.value = ""
  clearTimeout(timer)
  timer = setTimeout(async () => {
    try {
      const q = encodeURIComponent(query.value.trim())
      if (mode.value === "library") {
        const res = await api.get<{ hits: any[]; total: number }>(
          "/search?q=" + q + "&scope=" + scope.value + "&limit=30",
        )
        results.value = res.hits
        total.value = res.total
      } else {
        if (!sourceId.value) {
          error.value = "Please select a source"
          return
        }
        const res = await api.get<{ results: any[]; total: number }>(
          "/sources/" + encodeURIComponent(sourceId.value) + "/search?q=" + q + "&page=1&limit=30",
        )
        remoteResults.value = res.results
        remoteTotal.value = res.total
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : "Search failed"
    } finally {
      searching.value = false
    }
  }, 300)
}

async function syncRemote(item: any) {
  if (syncingUrl.value) return
  syncingUrl.value = item.url
  try {
    const res = await api.post<{ book_id: string }>("/sync/book", {
      source_id: item.source_id,
      url: item.url,
    })
    router.push("/books/" + res.book_id)
  } catch (e) {
    alert(e instanceof Error ? e.message : "Sync failed")
  } finally {
    syncingUrl.value = ""
  }
}

watch(query, () => {
  if (query.value.trim().length >= 2) doSearch()
})
watch(mode, () => {
  results.value = []
  remoteResults.value = []
  if (query.value.trim().length >= 2) doSearch()
})
watch(scope, () => {
  if (mode.value === "library" && query.value.trim().length >= 2) doSearch()
})
watch(sourceId, () => {
  if (mode.value === "sources" && query.value.trim().length >= 2) doSearch()
})

onMounted(async () => {
  await auth.fetchMe()
  await loadSources()
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-800 dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-3xl mx-auto px-4 py-8">
      <h1 class="text-2xl font-bold mb-6">Search</h1>

      <div class="flex gap-2 mb-4">
        <button
          v-for="m in (['library', 'sources'] as const)"
          :key="m"
          @click="mode = m"
          class="text-xs px-3 py-1 rounded-full transition-colors"
          :class="mode === m ? 'bg-accent text-white' : 'bg-gray-100 dark:bg-gray-700 text-muted dark:text-gray-400 hover:bg-gray-200'"
        >{{ m === 'library' ? 'Library' : 'Book Sources' }}</button>
      </div>

      <div class="flex gap-2 mb-4">
        <input
          v-model="query"
          type="search"
          placeholder="Search books, authors, content..."
          class="flex-1 px-4 py-2.5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-ink placeholder:text-muted dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-accent/30 text-sm"
          @keydown.enter="doSearch"
        />
        <button
          @click="doSearch"
          class="px-5 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:opacity-90 transition-opacity"
        >Search</button>
      </div>

      <div v-if="mode === 'sources'" class="mb-4">
        <label class="block text-xs font-medium text-muted dark:text-gray-400 mb-1.5">Source</label>
        <select
          v-model="sourceId"
          class="w-full px-3 py-2 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-sm"
        >
          <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
      </div>

      <div v-else class="flex gap-3 mb-6">
        <button
          v-for="s in (['books', 'chapters'] as const)"
          :key="s"
          @click="scope = s"
          class="text-xs px-3 py-1 rounded-full transition-colors"
          :class="scope === s ? 'bg-accent text-white' : 'bg-gray-100 dark:bg-gray-700 text-muted dark:text-gray-400 hover:bg-gray-200'"
        >{{ s === 'books' ? 'Books' : 'Chapters' }}</button>
      </div>

      <p v-if="error" class="text-sm text-red-600 mb-4">{{ error }}</p>
      <p v-if="searching" class="text-muted dark:text-gray-400 text-sm">Searching...</p>

      <template v-else-if="searched">
        <p class="text-sm text-muted dark:text-gray-400 mb-4">
          {{ mode === 'library' ? total : remoteTotal }} results for "{{ query }}"
        </p>

        <p v-if="(mode === 'library' ? results : remoteResults).length === 0" class="text-muted dark:text-gray-400">
          No results found.
        </p>

        <div
          v-if="mode === 'library' && results.length"
          class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900"
        >
          <div
            v-for="hit in results"
            :key="hit.id"
            class="px-4 py-3 hover:bg-accent/5 cursor-pointer transition-colors"
            @click="
              scope === 'books'
                ? router.push('/books/' + hit.id)
                : router.push('/books/' + hit.book_id + '/chapters/' + hit.id)
            "
          >
            <h3 class="text-sm font-medium mb-0.5">{{ hit.title }}</h3>
            <p v-if="hit.author" class="text-xs text-muted dark:text-gray-400">{{ hit.author }}</p>
            <p v-if="hit.content" class="text-xs text-muted dark:text-gray-400 mt-1 line-clamp-2">
              {{ hit.content.slice(0, 200) }}
            </p>
          </div>
        </div>

        <div
          v-if="mode === 'sources' && remoteResults.length"
          class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900"
        >
          <div v-for="item in remoteResults" :key="item.url" class="px-4 py-3">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <h3 class="text-sm font-medium mb-0.5 truncate">{{ item.name }}</h3>
                <p class="text-xs text-muted dark:text-gray-400">
                  {{ item.author }}{{ item.latest_chapter ? ' - ' + item.latest_chapter : '' }}
                </p>
                <p v-if="item.intro" class="text-xs text-muted dark:text-gray-400 mt-1 line-clamp-2">
                  {{ item.intro }}
                </p>
              </div>
              <button
                v-if="item.in_library"
                @click="router.push('/books/' + item.book_id)"
                class="shrink-0 px-3 py-1.5 rounded border border-accent text-accent text-xs hover:bg-accent/10"
              >Open</button>
              <button
                v-else-if="auth.isAdmin"
                @click="syncRemote(item)"
                :disabled="syncingUrl === item.url"
                class="shrink-0 px-3 py-1.5 rounded bg-accent text-white text-xs hover:opacity-90 disabled:opacity-50"
              >{{ syncingUrl === item.url ? 'Syncing...' : 'Sync' }}</button>
            </div>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>
