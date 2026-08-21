<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { api } from "../api/client"
import BookCard from "../components/BookCard.vue"
import NavBar from "../components/NavBar.vue"
import { useAuthStore } from "../stores/auth"
import type { Book } from "../stores/books"
import { useBooksStore } from "../stores/books"
import { useI18nStore } from "../stores/i18n"

type SearchField = "title" | "author" | "chapter_title" | "description" | "content" | "tags" | "category"
type MatchMode = "exact" | "fuzzy"
type SearchTab = "local" | "sources"

interface CategoryItem { id: string; name: string; color?: string | null }
interface SourceItem { id: string; name: string }
interface HomeSection { category_id: string; category_name: string; category_color?: string | null; total: number; books: Book[] }
interface HomeData { total: number; latest: Book[]; sections: HomeSection[] }
interface BookPage { items: Book[]; total: number; offset: number; limit: number }
interface SearchHit {
  type: "book" | "chapter"
  id: string
  book_id?: string
  title: string
  book_title?: string
  author?: string
  snippet?: string
  matched_fields?: string[]
  matched_chapter?: { id: string; book_id: string; title: string; snippet?: string }
}
interface Condition {
  enabled: boolean
  field: SearchField
  mode: MatchMode
  value: string
}
interface RemoteBook {
  source_id: string
  source_name: string
  name: string
  author: string
  url: string
  cover_url?: string | null
  intro?: string | null
  kind?: string | null
  latest_chapter?: string | null
  word_count?: string | null
  in_library: boolean
  book_id?: string | null
}

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const booksStore = useBooksStore()
const i18n = useI18nStore()

const categories = ref<CategoryItem[]>([])
const sources = ref<SourceItem[]>([])
const home = ref<HomeData | null>(null)
const homeSources = ref<{ source: SourceItem; total: number; books: Book[] }[]>([])
const page = ref<BookPage>({ items: [], total: 0, offset: 0, limit: 24 })
const results = ref<SearchHit[]>([])
const searchTotal = ref(0)
const loading = ref(false)
const error = ref("")
const selectedIds = ref<string[]>([])
const actionBusy = ref(false)
const searchQuery = ref("")
const searchField = ref<SearchField>("title")

// ---- advanced / source search state ----
const searchTab = ref<SearchTab>("local")
const advancedOpen = ref(false)
const advancedActive = ref(false)
const match = ref<"and" | "or">("and")
const conditions = ref<Condition[]>([
  { enabled: true, field: "title", mode: "exact", value: "" },
])
const advancedResults = ref<SearchHit[]>([])
const advancedTotal = ref(0)
const advancedSearching = ref(false)
const advancedError = ref("")
const sourceSearchId = ref("")
const sourceQuery = ref("")
const remoteResults = ref<RemoteBook[]>([])
const remoteTotal = ref(0)
const remoteSearching = ref(false)
const syncingUrl = ref("")

const activeCategory = computed(() => String(route.query.category || ""))
const activeSource = computed(() => String(route.query.source || route.query.source_id || ""))
const currentOffset = computed(() => Math.max(0, Number(route.query.offset || 0) || 0))
const isSearching = computed(() => Boolean(String(route.query.q || "").trim()))
const isBrowsing = computed(() => !isSearching.value && Boolean(activeCategory.value || activeSource.value))
const showCovers = computed(() => auth.user?.settings?.show_covers !== false)
const sourceNameMap = computed<Record<string, string>>(() => Object.fromEntries(sources.value.map((source) => [source.id, source.name])))
const activeCategoryItem = computed(() => categories.value.find((category) => category.name === activeCategory.value))
const allSelected = computed(() => page.value.items.length > 0 && page.value.items.every((book) => selectedIds.value.includes(book.id)))

const searchFields: { value: SearchField; label: string }[] = [
  { value: "title", label: "search_field_title" },
  { value: "author", label: "search_field_author" },
  { value: "chapter_title", label: "search_field_chapter_title" },
  { value: "description", label: "search_field_description" },
  { value: "content", label: "search_field_content" },
  { value: "tags", label: "search_field_tags" },
  { value: "category", label: "search_field_category" },
]
const fieldOptions = searchFields
const fuzzyFields: SearchField[] = ["title", "author", "chapter_title", "description", "content"]

async function loadNavigation() {
  const [categoryRows, sourceRows] = await Promise.all([
    api.get<CategoryItem[]>("/categories"),
    api.get<any[]>("/sources"),
  ])
  categories.value = categoryRows
  sources.value = sourceRows.map((source) => ({ id: source.id, name: source.name || source.id }))
  if (!sourceSearchId.value && sources.value.length) {
    sourceSearchId.value = sources.value[0].id
  }
}

async function loadHome() {
  home.value = await api.get<HomeData>("/books/home?section_limit=6")
  await loadHomeSources()
}

async function loadHomeSources() {
  homeSources.value = []
  const results = await Promise.all(
    sources.value.map(async (source) => {
      try {
        const res = await api.get<BookPage>("/books/browse?source_id=" + encodeURIComponent(source.id) + "&limit=6")
        return { source, total: res.total, books: res.items }
      } catch {
        return { source, total: 0, books: [] }
      }
    }),
  )
  homeSources.value = results.filter((r) => r.total > 0)
}

async function loadBrowse() {
  const params = new URLSearchParams({ offset: String(currentOffset.value), limit: "24" })
  if (activeCategory.value) params.set("category", activeCategory.value)
  if (activeSource.value) params.set("source_id", activeSource.value)
  page.value = await api.get<BookPage>("/books/browse?" + params)
}

async function loadSearch() {
  const q = String(route.query.q || "").trim()
  const field = (String(route.query.field || "title") as SearchField)
  searchQuery.value = q
  searchField.value = searchFields.some((item) => item.value === field) ? field : "title"
  const response = await api.post<{ hits: SearchHit[]; total: number }>("/search/advanced", {
    conditions: [{ field: searchField.value, mode: fuzzyFields.includes(searchField.value) ? "fuzzy" : "exact", value: q }],
    match: "and",
    scope: "all",
    offset: 0,
    limit: 40,
  })
  results.value = response.hits
  searchTotal.value = response.total
}

async function loadCurrentView() {
  loading.value = true
  error.value = ""
  selectedIds.value = []
  // URL navigation resets the local advanced/source search views
  advancedActive.value = false
  if (isSearching.value || isBrowsing.value) searchTab.value = "local"
  try {
    if (isSearching.value) await loadSearch()
    else if (isBrowsing.value) await loadBrowse()
    else await loadHome()
  } catch (e) {
    error.value = e instanceof Error ? e.message : i18n.t("search_failed")
  } finally {
    loading.value = false
  }
}

function submitSearch() {
  const q = searchQuery.value.trim()
  if (!q) {
    router.push({ path: "/books" })
    return
  }
  advancedActive.value = false
  advancedOpen.value = false
  router.push({ path: "/books", query: { q, field: searchField.value } })
}

function openCategory(name: string) {
  router.push({ path: "/books", query: { category: name } })
}

function changeSource(event: Event) {
  const source = (event.target as HTMLSelectElement).value
  router.push({ path: "/books", query: { ...(activeCategory.value ? { category: activeCategory.value } : {}), ...(source ? { source } : {}) } })
}

function searchByField(field: "author" | "tags" | "category", value: string) {
  router.push({ path: "/books", query: { field, q: value } })
}

function goToHit(hit: SearchHit) {
  if (hit.type === "book" && hit.matched_chapter) router.push("/books/" + hit.matched_chapter.book_id + "/chapters/" + hit.matched_chapter.id)
  else if (hit.type === "book") router.push("/books/" + hit.id)
  else if (hit.book_id) router.push("/books/" + hit.book_id + "/chapters/" + hit.id)
}

function toggleSelect(id: string) {
  selectedIds.value = selectedIds.value.includes(id) ? selectedIds.value.filter((item) => item !== id) : [...selectedIds.value, id]
}

function toggleAll() {
  selectedIds.value = allSelected.value ? [] : page.value.items.map((book) => book.id)
}

async function toggleFavorite(book: Book) {
  await booksStore.toggleFavorite(book)
}

async function batchFavorite() {
  if (!selectedIds.value.length) return
  actionBusy.value = true
  try {
    await api.post("/books/batch-favorite", { ids: selectedIds.value })
    await loadCurrentView()
  } finally { actionBusy.value = false }
}

async function batchDelete() {
  if (!selectedIds.value.length || !confirm(i18n.t("books_batch_delete_confirm", { n: selectedIds.value.length }))) return
  actionBusy.value = true
  try {
    await api.post("/books/batch-delete", { ids: selectedIds.value })
    await loadCurrentView()
  } finally { actionBusy.value = false }
}

async function deleteCategoryBooks() {
  const category = activeCategoryItem.value
  if (!category || !confirm(i18n.t("books_category_delete_confirm", { name: category.name, n: page.value.total }))) return
  actionBusy.value = true
  try {
    await api.post("/books/batch-delete-by-category", { category_id: category.id })
    await router.push("/books")
  } finally { actionBusy.value = false }
}

async function deleteSourceBooks() {
  if (!activeSource.value || !confirm(i18n.t("books_source_delete_confirm", { name: sourceNameMap.value[activeSource.value] || activeSource.value, n: page.value.total }))) return
  actionBusy.value = true
  try {
    await api.post("/books/batch-delete-by-source", { source_id: activeSource.value })
    await router.push("/books")
  } finally { actionBusy.value = false }
}

function changePage(offset: number) {
  router.push({ path: "/books", query: { ...route.query, offset: String(Math.max(0, offset)) } })
}

// ---------- advanced search ----------

function toggleAdvanced() {
  advancedOpen.value = !advancedOpen.value
  if (advancedOpen.value && conditions.value.length === 0) {
    conditions.value = [{ enabled: true, field: "title", mode: "exact", value: "" }]
  }
}

function activeConditions() {
  return conditions.value
    .filter((c) => c.enabled && c.value.trim())
    .map((c) => ({ field: c.field, mode: c.mode, value: c.value.trim() }))
}

function addCondition() {
  conditions.value.push({ enabled: true, field: "title", mode: "exact", value: "" })
}

function removeCondition(index: number) {
  conditions.value.splice(index, 1)
}

async function runAdvancedSearch() {
  const conds = activeConditions()
  if (conds.length === 0) {
    advancedError.value = i18n.t("search_condition_placeholder")
    return
  }
  advancedSearching.value = true
  advancedError.value = ""
  try {
    const res = await api.post<{ hits: SearchHit[]; total: number }>("/search/advanced", {
      conditions: conds,
      match: match.value,
      scope: "all",
      offset: 0,
      limit: 40,
    })
    advancedResults.value = res.hits
    advancedTotal.value = res.total
    advancedActive.value = true
  } catch (e) {
    advancedError.value = e instanceof Error ? e.message : i18n.t("search_failed")
  } finally {
    advancedSearching.value = false
  }
}

// ---------- source search (remote book sources) ----------

async function runSourceSearch() {
  const q = sourceQuery.value.trim()
  if (!q) return
  if (!sourceSearchId.value) {
    advancedError.value = i18n.t("search_select_source")
    return
  }
  remoteSearching.value = true
  advancedError.value = ""
  try {
    const res = await api.get<{ results: RemoteBook[]; total: number }>(
      "/sources/" + encodeURIComponent(sourceSearchId.value) + "/search?q=" + encodeURIComponent(q) + "&page=1&limit=30",
    )
    remoteResults.value = res.results || []
    remoteTotal.value = res.total
  } catch (e) {
    advancedError.value = e instanceof Error ? e.message : i18n.t("search_failed")
  } finally {
    remoteSearching.value = false
  }
}

async function syncRemoteBook(item: RemoteBook) {
  if (syncingUrl.value) return
  syncingUrl.value = item.url
  try {
    const res = await api.post<{ book_id: string }>("/sync/book", {
      source_id: item.source_id,
      url: item.url,
    })
    alert(i18n.t("search_remote_sync_done"))
    router.push("/books/" + res.book_id)
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t("search_sync_failed"))
  } finally {
    syncingUrl.value = ""
  }
}

watch(() => route.query, loadCurrentView, { deep: true })

onMounted(async () => {
  try { await loadNavigation() } catch { categories.value = []; sources.value = [] }
  await loadCurrentView()
})
</script>

<template>
  <div class="min-h-screen bg-paper text-ink dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="mx-auto max-w-6xl px-4 pb-12 pt-7">
      <header class="mb-6 text-center">
        <h1 class="text-2xl font-bold">{{ i18n.t('books_title') }}</h1>
        <p class="mt-1 text-sm text-muted dark:text-gray-400">{{ i18n.t('books_subtitle') }}</p>

        <div class="mx-auto mt-5 flex max-w-2xl items-center justify-center gap-2">
          <div class="flex rounded-lg border border-border bg-surface overflow-hidden dark:border-gray-700 dark:bg-gray-900">
            <button
              @click="searchTab = 'local'"
              class="px-3 py-2 text-xs transition-colors"
              :class="searchTab === 'local' ? 'bg-accent text-white' : 'text-muted hover:bg-black/5 dark:hover:bg-white/5'"
            >{{ i18n.t('search_tab_local') }}</button>
            <button
              @click="searchTab = 'sources'"
              class="px-3 py-2 text-xs transition-colors"
              :class="searchTab === 'sources' ? 'bg-accent text-white' : 'text-muted hover:bg-black/5 dark:hover:bg-white/5'"
            >{{ i18n.t('search_tab_sources') }}</button>
          </div>
        </div>

        <form v-if="searchTab === 'local'" @submit.prevent="submitSearch" class="mx-auto mt-3 flex max-w-2xl overflow-hidden rounded-lg border border-border bg-surface shadow-sm focus-within:border-accent dark:border-gray-700 dark:bg-gray-900">
          <select v-model="searchField" class="border-r border-border bg-transparent px-3 text-xs outline-none dark:border-gray-700">
            <option v-for="field in searchFields" :key="field.value" :value="field.value">{{ i18n.t(field.label) }}</option>
          </select>
          <input v-model="searchQuery" type="search" :placeholder="i18n.t('search_placeholder')" class="min-w-0 flex-1 bg-transparent px-4 py-3 text-sm outline-none" />
          <button type="submit" class="flex w-12 items-center justify-center bg-accent text-lg text-white" :title="i18n.t('nav_search')">⌕</button>
        </form>

        <form v-else @submit.prevent="runSourceSearch" class="mx-auto mt-3 flex max-w-2xl overflow-hidden rounded-lg border border-border bg-surface shadow-sm focus-within:border-accent dark:border-gray-700 dark:bg-gray-900">
          <select v-model="sourceSearchId" class="max-w-36 border-r border-border bg-transparent px-3 py-3 text-xs outline-none dark:border-gray-700">
            <option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}</option>
          </select>
          <input v-model="sourceQuery" type="search" :placeholder="i18n.t('search_source_query_placeholder')" class="min-w-0 flex-1 bg-transparent px-4 py-3 text-sm outline-none" />
          <button type="submit" class="flex w-12 items-center justify-center bg-accent text-lg text-white" :title="i18n.t('search_button')">⌕</button>
        </form>

        <div v-if="searchTab === 'local'" class="mt-3 flex flex-wrap items-center justify-center gap-2 text-xs">
          <button
            @click="toggleAdvanced"
            class="rounded border px-3 py-1.5 transition-colors"
            :class="advancedOpen ? 'border-accent text-accent' : 'border-border text-muted hover:bg-accent/5 dark:border-gray-700 dark:text-gray-400'"
          >{{ advancedOpen ? i18n.t('books_search_advanced_off') : i18n.t('search_advanced') }}</button>
          <span class="text-muted dark:text-gray-400">{{ i18n.t('books_advanced_search_hint') }}</span>
        </div>
      </header>

      <div v-if="searchTab === 'local' && advancedOpen" class="mx-auto mb-6 max-w-2xl rounded-lg border border-border bg-surface p-4 dark:border-gray-700 dark:bg-gray-900">
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <div class="flex rounded-lg border border-border dark:border-gray-700 overflow-hidden">
            <button
              @click="match = 'and'"
              class="text-xs px-3 py-2 transition-colors"
              :class="match === 'and' ? 'bg-accent text-white' : 'text-muted hover:bg-gray-100 dark:hover:bg-gray-800'"
            >{{ i18n.t('search_match_and') }}</button>
            <button
              @click="match = 'or'"
              class="text-xs px-3 py-2 transition-colors"
              :class="match === 'or' ? 'bg-accent text-white' : 'text-muted hover:bg-gray-100 dark:hover:bg-gray-800'"
            >{{ i18n.t('search_match_or') }}</button>
          </div>
          <span class="text-muted dark:text-gray-400 text-xs">{{ i18n.t('search_mode_label') }}</span>
        </div>

        <div class="space-y-3">
          <div
            v-for="(c, i) in conditions"
            :key="i"
            class="flex flex-wrap items-center gap-2 rounded-lg border border-border dark:border-gray-700 bg-paper dark:bg-gray-800 px-3 py-2"
          >
            <label class="flex items-center gap-1.5 text-xs text-muted dark:text-gray-400 cursor-pointer">
              <input v-model="c.enabled" type="checkbox" class="accent-accent h-4 w-4" />
              {{ i18n.t('search_condition') }} {{ i + 1 }}
            </label>
            <select
              v-model="c.field"
              class="px-2 py-1.5 rounded-md border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-xs focus:outline-none"
            >
              <option v-for="f in fieldOptions" :key="f.value" :value="f.value">{{ i18n.t(f.label) }}</option>
            </select>
            <select
              v-model="c.mode"
              class="px-2 py-1.5 rounded-md border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-xs focus:outline-none"
            >
              <option value="exact">{{ i18n.t('search_mode_exact') }}</option>
              <option value="fuzzy">{{ i18n.t('search_mode_fuzzy') }}</option>
            </select>
            <input
              v-model="c.value"
              type="search"
              :placeholder="i18n.t('search_condition_placeholder')"
              class="flex-1 min-w-[180px] px-3 py-1.5 rounded-md border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-sm focus:outline-none focus:ring-2 focus:ring-accent/30"
              @keydown.enter="runAdvancedSearch"
            />
            <button
              v-if="conditions.length > 1"
              @click="removeCondition(i)"
              class="text-xs px-2 py-1.5 rounded-md text-muted dark:text-gray-400 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-600 transition-colors"
            >{{ i18n.t('search_condition_remove') }}</button>
          </div>
        </div>

        <div class="mt-3 flex flex-wrap items-center gap-3">
          <button
            @click="addCondition"
            class="text-xs px-3 py-2 rounded-lg border border-accent/40 text-accent hover:bg-accent/5 transition-colors"
          >{{ i18n.t('search_conditions_add') }}</button>
          <button
            @click="runAdvancedSearch"
            :disabled="advancedSearching"
            class="text-xs px-4 py-2 rounded-lg bg-accent text-white hover:opacity-90 transition-opacity disabled:opacity-50"
          >{{ i18n.t('search_button') }}</button>
          <span v-if="advancedSearching" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('search_searching') }}</span>
        </div>
      </div>

      <nav class="mb-7 flex items-center gap-2 overflow-x-auto border-y border-border py-3 dark:border-gray-800">
        <button @click="router.push('/books')" class="shrink-0 rounded px-3 py-1.5 text-xs" :class="!activeCategory && !activeSource && !isSearching && !advancedActive ? 'bg-accent text-white' : 'text-muted hover:bg-black/5 dark:text-gray-400 dark:hover:bg-white/5'">{{ i18n.t('books_all_categories') }}</button>
        <button v-for="category in categories" :key="category.id" @click="openCategory(category.name)" class="shrink-0 rounded px-3 py-1.5 text-xs" :class="activeCategory === category.name ? 'bg-accent text-white' : 'text-muted hover:bg-black/5 dark:text-gray-400 dark:hover:bg-white/5'">{{ category.name }}</button>
      </nav>

      <p v-if="loading" class="py-16 text-center text-sm text-muted dark:text-gray-400">{{ i18n.t('home_loading') }}</p>
      <p v-else-if="error" class="py-10 text-center text-sm text-red-600">{{ error }}</p>

      <!-- source search results -->
      <template v-else-if="searchTab === 'sources'">
        <div class="mb-4 flex items-center justify-between">
          <h2 class="text-lg font-semibold">{{ i18n.t('search_book_sources') }}</h2>
          <span v-if="remoteSearching" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('search_searching') }}</span>
        </div>
        <p v-if="advancedError" class="mb-4 text-sm text-red-600">{{ advancedError }}</p>
        <p v-else-if="sourceQuery.trim() && remoteResults.length === 0 && !remoteSearching" class="py-12 text-center text-sm text-muted dark:text-gray-400">{{ i18n.t('search_remote_empty') }}</p>
        <div v-else-if="remoteResults.length" class="divide-y divide-border border border-border rounded-lg bg-surface overflow-hidden dark:divide-gray-800 dark:border-gray-700 dark:bg-gray-900">
          <div v-for="item in remoteResults" :key="item.source_id + '-' + item.url" class="flex items-center gap-3 px-4 py-3">
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-sm font-medium truncate">{{ item.name }}</h3>
                <span v-if="item.in_library" class="rounded bg-green-100 px-1.5 py-0.5 text-[10px] text-green-700 dark:bg-green-900 dark:text-green-300">{{ i18n.t('search_remote_in_library') }}</span>
              </div>
              <p class="mt-0.5 text-xs text-muted dark:text-gray-400">
                {{ item.author }}
                <span v-if="item.latest_chapter" class="ml-2">{{ item.latest_chapter }}</span>
              </p>
              <p v-if="item.intro" class="mt-1 line-clamp-2 text-xs text-muted dark:text-gray-400">{{ item.intro }}</p>
            </div>
            <div class="shrink-0">
              <button
                v-if="!item.in_library"
                @click="syncRemoteBook(item)"
                :disabled="syncingUrl === item.url"
                class="rounded bg-accent px-3 py-1.5 text-xs text-white disabled:opacity-50"
              >{{ syncingUrl === item.url ? i18n.t('search_remote_syncing') : i18n.t('search_remote_sync') }}</button>
              <button
                v-else-if="item.book_id"
                @click="router.push('/books/' + item.book_id)"
                class="rounded border border-accent/50 px-3 py-1.5 text-xs text-accent"
              >{{ i18n.t('search_open') }}</button>
            </div>
          </div>
        </div>
      </template>

      <!-- advanced search results -->
      <template v-else-if="advancedActive">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <h2 class="text-lg font-semibold">{{ i18n.t('search_results_count', { n: advancedTotal }) }}</h2>
            <p v-if="advancedError" class="mt-1 text-sm text-red-600">{{ advancedError }}</p>
          </div>
        </div>
        <p v-if="!advancedSearching && advancedResults.length === 0" class="py-12 text-center text-sm text-muted dark:text-gray-400">{{ i18n.t('search_no_results') }}</p>
        <div v-if="advancedResults.length" class="divide-y divide-border border-y border-border dark:divide-gray-800 dark:border-gray-800">
          <button v-for="hit in advancedResults" :key="hit.type + '-' + hit.id" @click="goToHit(hit)" class="block w-full px-2 py-4 text-left transition-colors hover:bg-accent/5">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">{{ hit.type === 'book' ? i18n.t('search_books') : i18n.t('search_chapters') }}</span>
              <h3 class="text-sm font-medium">{{ hit.type === 'book' ? hit.title : hit.book_title }}</h3>
              <span v-for="field in hit.matched_fields || []" :key="field" class="text-[10px] text-muted dark:text-gray-500">{{ i18n.t('search_field_' + field) }}</span>
            </div>
            <p v-if="hit.type === 'chapter'" class="mt-1 text-xs font-medium">{{ hit.title }}</p>
            <p v-if="hit.author" class="mt-1 text-xs text-muted dark:text-gray-400">{{ hit.author }}</p>
            <p v-if="hit.snippet" class="mt-1 line-clamp-2 text-xs text-muted dark:text-gray-400">{{ hit.snippet }}</p>
            <div v-if="hit.matched_chapter" class="mt-2 rounded bg-accent/5 px-2.5 py-2 border border-accent/10">
              <p class="text-xs font-medium">{{ hit.matched_chapter.title }}</p>
              <p v-if="hit.matched_chapter.snippet" class="mt-0.5 line-clamp-2 text-xs text-muted dark:text-gray-400">{{ hit.matched_chapter.snippet }}</p>
            </div>
          </button>
        </div>
      </template>

      <!-- quick search results -->
      <template v-else-if="isSearching">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <button @click="router.push('/books')" class="text-xs text-accent hover:underline">{{ i18n.t('books_back_home') }}</button>
            <h2 class="mt-1 text-lg font-semibold">{{ i18n.t('search_results_count', { n: searchTotal }) }}</h2>
          </div>
        </div>
        <p v-if="!results.length" class="py-12 text-center text-sm text-muted dark:text-gray-400">{{ i18n.t('search_no_results') }}</p>
        <div v-else class="divide-y divide-border border-y border-border dark:divide-gray-800 dark:border-gray-800">
          <button v-for="hit in results" :key="hit.type + '-' + hit.id" @click="goToHit(hit)" class="block w-full px-2 py-4 text-left transition-colors hover:bg-accent/5">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">{{ hit.type === 'book' ? i18n.t('search_books') : i18n.t('search_chapters') }}</span>
              <h3 class="text-sm font-medium">{{ hit.type === 'book' ? hit.title : hit.book_title }}</h3>
              <span v-for="field in hit.matched_fields || []" :key="field" class="text-[10px] text-muted dark:text-gray-500">{{ i18n.t('search_field_' + field) }}</span>
            </div>
            <p v-if="hit.type === 'chapter'" class="mt-1 text-xs font-medium">{{ hit.title }}</p>
            <p v-if="hit.author" class="mt-1 text-xs text-muted dark:text-gray-400">{{ hit.author }}</p>
            <p v-if="hit.snippet" class="mt-1 line-clamp-2 text-xs text-muted dark:text-gray-400">{{ hit.snippet }}</p>
          </button>
        </div>
      </template>

      <template v-else-if="isBrowsing">
        <div class="mb-5 flex flex-wrap items-end justify-between gap-3">
          <div>
            <button @click="router.push('/books')" class="text-xs text-accent hover:underline">{{ i18n.t('books_back_home') }}</button>
            <h2 class="mt-1 text-xl font-semibold">{{ activeCategory || sourceNameMap[activeSource] || i18n.t('books_title') }}</h2>
            <p class="mt-1 text-xs text-muted dark:text-gray-400">{{ i18n.t('home_books_count', { n: page.total }) }}</p>
          </div>
          <select :value="activeSource" @change="changeSource" class="rounded border border-border bg-surface px-3 py-2 text-xs dark:border-gray-700 dark:bg-gray-900">
            <option value="">{{ i18n.t('books_all_sources') }}</option>
            <option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}</option>
          </select>
        </div>

        <div v-if="auth.isAdmin" class="mb-4 flex flex-wrap items-center gap-2 border-y border-border py-3 text-xs dark:border-gray-800">
          <label class="inline-flex items-center gap-1.5"><input type="checkbox" :checked="allSelected" @change="toggleAll" class="rounded" />{{ i18n.t('books_select_all') }}</label>
          <span class="text-muted dark:text-gray-400">{{ i18n.t('books_selected_count', { n: selectedIds.length }) }}</span>
          <button v-if="selectedIds.length" @click="batchFavorite" :disabled="actionBusy" class="rounded border border-accent px-2 py-1 text-accent">{{ i18n.t('books_batch_add_shelf') }}</button>
          <button v-if="selectedIds.length" @click="batchDelete" :disabled="actionBusy" class="rounded bg-red-600 px-2 py-1 text-white">{{ i18n.t('books_batch_delete') }}</button>
          <button v-if="activeCategoryItem" @click="deleteCategoryBooks" :disabled="actionBusy || !page.total" class="ml-auto rounded border border-red-300 px-2 py-1 text-red-600 disabled:opacity-40">{{ i18n.t('books_category_delete') }}</button>
          <button v-if="activeSource" @click="deleteSourceBooks" :disabled="actionBusy || !page.total" class="rounded border border-red-300 px-2 py-1 text-red-600 disabled:opacity-40">{{ i18n.t('books_source_delete', { n: page.total }) }}</button>
        </div>

        <p v-if="!page.items.length" class="py-12 text-center text-sm text-muted dark:text-gray-400">{{ i18n.t('books_filter_empty') }}</p>
        <div v-else class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <BookCard v-for="book in page.items" :key="book.id" :book="book" :show-cover="showCovers" :source-name="book.source_id ? sourceNameMap[book.source_id] : ''" :selectable="auth.isAdmin" :selected="selectedIds.includes(book.id)" @select="toggleSelect" @favorite="toggleFavorite" @search="searchByField" />
        </div>
        <div v-if="page.total > page.limit" class="mt-8 flex items-center justify-center gap-3 text-xs">
          <button @click="changePage(page.offset - page.limit)" :disabled="page.offset === 0" class="rounded border border-border px-3 py-2 disabled:opacity-40 dark:border-gray-700">{{ i18n.t('books_previous') }}</button>
          <span>{{ Math.floor(page.offset / page.limit) + 1 }} / {{ Math.ceil(page.total / page.limit) }}</span>
          <button @click="changePage(page.offset + page.limit)" :disabled="page.offset + page.limit >= page.total" class="rounded border border-border px-3 py-2 disabled:opacity-40 dark:border-gray-700">{{ i18n.t('books_next') }}</button>
        </div>
      </template>

      <template v-else-if="home">
        <section v-if="home.latest.length" class="mb-10">
          <div class="mb-4 flex items-end justify-between">
            <div><h2 class="text-lg font-semibold">{{ i18n.t('books_latest') }}</h2><p class="mt-1 text-xs text-muted dark:text-gray-400">{{ i18n.t('home_books_count', { n: home.total }) }}</p></div>
          </div>
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <BookCard v-for="book in home.latest" :key="book.id" :book="book" :show-cover="showCovers" :source-name="book.source_id ? sourceNameMap[book.source_id] : ''" @favorite="toggleFavorite" @search="searchByField" />
          </div>
        </section>

        <section v-for="section in home.sections" :key="section.category_id" class="mb-10 border-t border-border pt-6 dark:border-gray-800">
          <div class="mb-4 flex items-center justify-between">
            <div class="flex items-center gap-2"><span class="h-4 w-1 rounded" :style="{ backgroundColor: section.category_color || '#9b4a32' }"></span><h2 class="text-lg font-semibold">{{ section.category_name }}</h2><span class="text-xs text-muted dark:text-gray-500">{{ section.total }}</span></div>
            <button @click="openCategory(section.category_name)" class="text-xs text-accent hover:underline">{{ i18n.t('books_view_all') }} ›</button>
          </div>
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <BookCard v-for="book in section.books" :key="book.id" :book="book" :show-cover="showCovers" :source-name="book.source_id ? sourceNameMap[book.source_id] : ''" @favorite="toggleFavorite" @search="searchByField" />
          </div>
        </section>

        <section v-if="homeSources.length" class="mb-10 border-t border-border pt-6 dark:border-gray-800">
          <div class="mb-4 flex items-center justify-between">
            <div class="flex items-center gap-2"><h2 class="text-lg font-semibold">{{ i18n.t('home_by_source') }}</h2></div>
          </div>
          <div v-for="section in homeSources" :key="section.source.id" class="mb-8">
            <div class="mb-3 flex items-center justify-between">
              <h3 class="text-sm font-semibold text-muted dark:text-gray-400">{{ section.source.name }}</h3>
              <button @click="router.push({ path: '/books', query: { source: section.source.id } })" class="text-xs text-accent hover:underline">{{ i18n.t('books_view_all_source') }} ({{ section.total }}) ›</button>
            </div>
            <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <BookCard v-for="book in section.books" :key="book.id" :book="book" :show-cover="showCovers" :source-name="book.source_id ? sourceNameMap[book.source_id] : ''" @favorite="toggleFavorite" @search="searchByField" />
            </div>
          </div>
        </section>

        <div v-if="home.total === 0" class="py-16 text-center"><p class="text-muted dark:text-gray-400">{{ i18n.t('books_empty') }}</p><router-link to="/settings" class="mt-3 inline-block text-sm text-accent hover:underline">{{ i18n.t('books_empty_hint') }}</router-link></div>
      </template>
    </main>
  </div>
</template>
