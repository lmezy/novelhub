<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"
import SnippetText from "../components/SnippetText.vue"
import { useRoute, useRouter } from "vue-router"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"

type SearchField = "title" | "author" | "chapter_title" | "description" | "content"
  | "tags"
  | "category"
type MatchMode = "exact" | "fuzzy"

interface Condition {
  enabled: boolean
  field: SearchField
  mode: MatchMode
  value: string
}

interface SearchHit {
  type: "book" | "chapter"
  id: string
  book_id?: string
  title: string
  book_title?: string
  author?: string
  chapter_number?: number
  description?: string
  snippet?: string
  // ``/api/search/advanced`` attaches the local cover (the search index does
  // not carry one), so a result row can show the book's picture.
  cover?: string | null
  cover_url?: string | null
  matched_fields?: string[]
  matched_chapter?: {
    id: string
    book_id: string
    title: string
    chapter_number?: number
    content?: string
    snippet?: string
  }
}

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const i18n = useI18nStore()

const match = ref<"and" | "or">("and")
const conditions = ref<Condition[]>([
  { enabled: true, field: "title", mode: "exact", value: "" },
])
const tags = ref<{ id: string; name: string }[]>([])
const results = ref<SearchHit[]>([])
const total = ref(0)
const searching = ref(false)
const searched = ref(false)
const error = ref("")

let timer: ReturnType<typeof setTimeout>

const fieldOptions: { value: SearchField; labelKey: string }[] = [
  { value: "title", labelKey: "search_field_title" },
  { value: "author", labelKey: "search_field_author" },
  { value: "chapter_title", labelKey: "search_field_chapter_title" },
  { value: "description", labelKey: "search_field_description" },
  { value: "content", labelKey: "search_field_content" },
  { value: "tags", labelKey: "search_field_tags" },
  { value: "category", labelKey: "search_field_category" },
]

function activeConditions() {
  return conditions.value
    .filter((c) => c.enabled && c.value.trim())
    .map((c) => ({ field: c.field, mode: c.mode, value: c.value.trim() }))
}

// The words to mark inside a snippet.  The API anchors the excerpt at the first
// match, so this only has to make it obvious; a term that is not in an excerpt
// simply marks nothing.
const snippetTerms = computed(() => activeConditions().map((c) => c.value))

async function loadTags() {
  try {
    tags.value = await api.get<{ id: string; name: string }[]>("/tags?limit=200")
  } catch {
    tags.value = []
  }
}

const pageSize = 30
const offset = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const currentPage = computed(() => Math.floor(offset.value / pageSize) + 1)
// Mirrors the current page so 上一页 / 下一页 and the jump box stay in sync.
// Without this page, a result list was a dead end: the only way to page 12 of a
// 300-hit search was eleven clicks on 下一页.
const pageJump = ref(String(currentPage.value))

watch(currentPage, (n) => {
  pageJump.value = String(n)
})

function doSearch(resetPage = true) {
  const conds = activeConditions()
  if (conds.length === 0) {
    searched.value = false
    results.value = []
    total.value = 0
    return
  }
  if (resetPage) offset.value = 0
  searching.value = true
  searched.value = true
  error.value = ""
  clearTimeout(timer)
  timer = setTimeout(async () => {
    try {
      const res = await api.post<{ hits: SearchHit[]; total: number }>(
        "/search/advanced",
        {
          conditions: conds,
          match: match.value,
          scope: "all",
          offset: offset.value,
          limit: pageSize,
        },
      )
      results.value = res.hits
      total.value = res.total
    } catch (e) {
      error.value = e instanceof Error ? e.message : i18n.t("search_failed")
    } finally {
      searching.value = false
    }
  }, 250)
}

// ``doSearch`` takes a flag, and a template handler would pass its event as
// that flag, so the "search now" button / Enter key go through this wrapper.
function doSearchNow() {
  doSearch(true)
}

function changePage(nextOffset: number) {
  const clamped = Math.max(0, Math.min(nextOffset, (totalPages.value - 1) * pageSize))
  // Write the page back *before* the no-op check: a jump that clamps onto the
  // page already on screen used to leave the typed number in the box, so the
  // next 跳转 moved somewhere the box did not describe.
  pageJump.value = String(Math.floor(clamped / pageSize) + 1)
  if (clamped === offset.value) return
  offset.value = clamped
  doSearch(false)
}

function jumpToTypedPage() {
  // ``Number("")`` is 0, not NaN, so a cleared box has to be caught explicitly.
  const raw = String(pageJump.value).trim()
  const typed = raw ? Math.trunc(Number(raw)) : Number.NaN
  if (!Number.isFinite(typed) || typed < 1) {
    pageJump.value = String(currentPage.value)
    return
  }
  const target = Math.min(typed, totalPages.value)
  pageJump.value = String(target)
  changePage((target - 1) * pageSize)
}

// Any edit to the conditions is a *new* query, so it starts from page 1.
watch([conditions, match], () => doSearch(true), { deep: true })

function addCondition() {
  conditions.value.push({ enabled: true, field: "title", mode: "exact", value: "" })
}

function removeCondition(index: number) {
  conditions.value.splice(index, 1)
}

function goToHit(hit: SearchHit) {
  if (hit.type === "book" && hit.matched_chapter) {
    router.push("/books/" + hit.matched_chapter.book_id + "/chapters/" + hit.matched_chapter.id)
  } else if (hit.type === "book") {
    router.push("/books/" + hit.id)
  } else if (hit.book_id) {
    router.push("/books/" + hit.book_id + "/chapters/" + hit.id)
  }
}

// A chapter hit lands in the reader, which has no way back to the book's own
// page (chapter list / metadata).  This is the id that button opens instead.
function hitBookId(hit: SearchHit) {
  if (hit.type === "book") return hit.matched_chapter?.book_id || hit.id
  return hit.book_id || ""
}

// An empty or broken cover falls back to the title placeholder rather than a
// broken-image icon (same rule as the book cards).
function hitCover(hit: SearchHit) {
  return hit.cover || hit.cover_url || ""
}

function hitCoverFailed(hit: SearchHit) {
  hit.cover = ""
  hit.cover_url = ""
}

function applyQuery() {
  const field = (route.query.field as string) || ""
  const q = (route.query.q as string) || ""
  if (
    q &&
    ["title", "author", "chapter_title", "description", "content", "tags", "category"].includes(field)
  ) {
    const fuzzyFields = ["title", "author", "description", "content", "chapter_title"]
    conditions.value = [{
      enabled: true,
      field: field as SearchField,
      mode: fuzzyFields.includes(field) ? "fuzzy" : "exact",
      value: q,
    }]
    match.value = "and"
    doSearch()
    return
  }
  if (!conditions.value.length || !conditions.value[0].value) {
    conditions.value = [{ enabled: true, field: "title", mode: "exact", value: "" }]
    searched.value = false
    results.value = []
    total.value = 0
  }
}

watch(() => route.query, applyQuery)

onMounted(async () => {
  await auth.fetchMe()
  await loadTags()
  applyQuery()
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-3xl mx-auto px-4 py-8">
      <h1 class="text-2xl font-bold mb-6">{{ i18n.t('search_title') }}</h1>

      <div class="mb-4 flex flex-wrap items-center gap-2">
        <div class="flex rounded-lg border border-border dark:border-gray-700 overflow-hidden">
          <button
            @click="match = 'and'"
            class="text-xs px-3 py-2 transition-colors"
            :class="match === 'and'
              ? 'bg-accent text-white'
              : 'bg-surface dark:bg-gray-900 text-muted dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
          >{{ i18n.t('search_match_and') }}</button>
          <button
            @click="match = 'or'"
            class="text-xs px-3 py-2 transition-colors"
            :class="match === 'or'
              ? 'bg-accent text-white'
              : 'bg-surface dark:bg-gray-900 text-muted dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
          >{{ i18n.t('search_match_or') }}</button>
        </div>
      </div>

      <div class="space-y-3 mb-3">
        <div
          v-for="(c, i) in conditions"
          :key="i"
          class="flex flex-wrap items-center gap-2 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 px-3 py-2"
        >
          <label class="flex items-center gap-1.5 text-xs text-muted dark:text-gray-400 cursor-pointer">
            <input v-model="c.enabled" type="checkbox" class="accent-accent h-4 w-4" />
            {{ i18n.t('search_condition') }} {{ i + 1 }}
          </label>
          <select
            v-model="c.field"
            class="px-2 py-1.5 rounded-md border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-xs focus:outline-none"
          >
            <option v-for="f in fieldOptions" :key="f.value" :value="f.value">
              {{ i18n.t(f.labelKey) }}
            </option>
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
            :list="c.field === 'tags' ? 'search-tag-options' : undefined"
            :placeholder="i18n.t('search_condition_placeholder')"
            class="flex-1 min-w-[180px] px-3 py-1.5 rounded-md border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-sm focus:outline-none focus:ring-2 focus:ring-accent/30"
            @keydown.enter="doSearchNow"
          />
          <button
            v-if="conditions.length > 1"
            @click="removeCondition(i)"
            class="text-xs px-2 py-1.5 rounded-md text-muted dark:text-gray-400 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-600 transition-colors"
          >{{ i18n.t('search_condition_remove') }}</button>
        </div>
      </div>

      <datalist id="search-tag-options">
        <option v-for="t in tags" :key="t.id" :value="t.name">{{ t.name }}</option>
      </datalist>

      <div class="flex flex-wrap items-center gap-3 mb-6">
        <button
          @click="addCondition"
          class="text-xs px-3 py-2 rounded-lg border border-accent/40 text-accent hover:bg-accent/5 transition-colors"
        >{{ i18n.t('search_conditions_add') }}</button>
        <button
          @click="doSearchNow"
          class="text-xs px-4 py-2 rounded-lg bg-accent text-white hover:opacity-90 transition-opacity"
        >{{ i18n.t('search_button') }}</button>
      </div>

      <p v-if="error" class="text-sm text-red-600 mb-4">{{ error }}</p>
      <p v-if="searching" class="text-muted dark:text-gray-400 text-sm">{{ i18n.t('search_searching') }}</p>

      <template v-else-if="searched">
        <p class="text-sm text-muted dark:text-gray-400 mb-4">
          {{ i18n.t('search_results_count', { n: total }) }}
        </p>

        <p v-if="results.length === 0" class="text-muted dark:text-gray-400">
          {{ i18n.t('search_no_results') }}
        </p>

        <div
          v-if="results.length"
          class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900 overflow-hidden"
        >
          <div
            v-for="hit in results"
            :key="hit.type + '-' + hit.id"
            class="flex items-start gap-2 px-4 py-3 hover:bg-accent/5 transition-colors"
          >
            <div @click="goToHit(hit)" class="flex min-w-0 flex-1 items-start gap-3 cursor-pointer">
              <div class="h-[74px] w-[56px] shrink-0 overflow-hidden rounded bg-gray-100 dark:bg-gray-800">
                <img v-if="hitCover(hit)" :src="hitCover(hit)" :alt="hit.type === 'book' ? hit.title : (hit.book_title || hit.title)" class="h-full w-full object-cover" loading="lazy" @error="hitCoverFailed(hit)" />
                <div v-else class="flex h-full items-center justify-center px-1 text-center text-[10px] leading-tight text-muted dark:text-gray-500">{{ hit.type === 'book' ? hit.title : (hit.book_title || hit.title) }}</div>
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2 mb-1">
                  <span class="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent">
                    {{ hit.type === 'book' ? i18n.t('search_books') : i18n.t('search_chapters') }}
                  </span>
                  <h3 class="text-sm font-medium">{{ hit.type === 'book' ? hit.title : hit.book_title }}</h3>
                  <span
                    v-for="field in hit.matched_fields || []"
                    :key="field"
                    class="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/60 text-amber-700 dark:text-amber-300"
                  >{{ i18n.t('search_field_' + field) }}</span>
                </div>
                <p v-if="hit.type === 'chapter' && hit.title" class="text-xs font-medium mb-0.5">
                  <span class="text-muted dark:text-gray-400">{{ i18n.t('search_field_chapter_title') }}:</span>
                  {{ hit.title }}
                </p>
                <p v-if="hit.author" class="text-xs text-muted dark:text-gray-400">{{ hit.author }}</p>
                <p v-if="hit.snippet" class="text-xs text-muted dark:text-gray-400 mt-1 line-clamp-2">
                  <SnippetText :text="hit.snippet" :terms="snippetTerms" />
                </p>
                <div
                  v-if="hit.matched_chapter"
                  class="mt-2 px-2.5 py-2 rounded bg-accent/5 border border-accent/10"
                >
                  <p class="text-xs font-medium mb-0.5">
                    <span class="text-muted dark:text-gray-400">{{ i18n.t('search_field_chapter_title') }}:</span>
                    {{ hit.matched_chapter.title }}
                  </p>
                  <p v-if="hit.matched_chapter.snippet" class="text-xs text-muted dark:text-gray-400 line-clamp-2">
                    <span class="text-muted dark:text-gray-400">{{ i18n.t('search_field_content') }}:</span>
                    <SnippetText :text="hit.matched_chapter.snippet" :terms="snippetTerms" />
                  </p>
                </div>
              </div>
            </div>
            <button
              v-if="hitBookId(hit)"
              @click="router.push('/books/' + hitBookId(hit))"
              class="shrink-0 px-2 py-1 rounded border border-border dark:border-gray-700 text-[11px] text-muted dark:text-gray-400 hover:border-accent hover:text-accent transition-colors"
              :title="i18n.t('search_open_book')"
            >{{ i18n.t('search_books') }}</button>
          </div>
        </div>

        <!-- Outside the results guard on purpose: a page whose slice came back
             empty (deep page past the scanned window) must still offer a way
             back, instead of stranding the user on "没有找到结果". -->
        <div v-if="total > pageSize" class="mt-6">
          <p v-if="results.length === 0" class="mb-3 text-center text-xs text-muted dark:text-gray-500">
            {{ i18n.t('search_page_empty') }}
          </p>
          <div class="flex flex-wrap items-center justify-center gap-3 text-xs">
            <button
              @click="changePage(offset - pageSize)"
              :disabled="currentPage <= 1"
              class="rounded border border-border dark:border-gray-700 px-3 py-2 disabled:opacity-40"
            >{{ i18n.t('books_previous') }}</button>
            <span>{{ currentPage }} / {{ totalPages }}</span>
            <button
              @click="changePage(offset + pageSize)"
              :disabled="currentPage >= totalPages"
              class="rounded border border-border dark:border-gray-700 px-3 py-2 disabled:opacity-40"
            >{{ i18n.t('books_next') }}</button>
            <span class="inline-flex items-center gap-1">
              {{ i18n.t('search_goto_page') }}
              <input
                v-model="pageJump"
                type="number"
                min="1"
                :max="totalPages"
                class="w-16 rounded border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 px-2 py-1.5 text-xs"
                @keydown.enter="jumpToTypedPage"
              />
              <button
                @click="jumpToTypedPage"
                class="rounded border border-accent/50 px-2 py-1.5 text-accent"
              >{{ i18n.t('search_goto') }}</button>
            </span>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>
