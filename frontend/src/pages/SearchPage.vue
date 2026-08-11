<script setup lang="ts">
import { onMounted, ref, watch } from "vue"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"
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

async function loadTags() {
  try {
    tags.value = await api.get<{ id: string; name: string }[]>("/tags?limit=200")
  } catch {
    tags.value = []
  }
}

function doSearch() {
  const conds = activeConditions()
  if (conds.length === 0) {
    searched.value = false
    results.value = []
    total.value = 0
    return
  }
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
          offset: 0,
          limit: 30,
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

watch([conditions, match], () => doSearch(), { deep: true })

function addCondition() {
  conditions.value.push({ enabled: true, field: "title", mode: "exact", value: "" })
  doSearch()
}

function removeCondition(index: number) {
  conditions.value.splice(index, 1)
  doSearch()
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
            @keydown.enter="doSearch"
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
          @click="doSearch"
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
            @click="goToHit(hit)"
            class="px-4 py-3 hover:bg-accent/5 cursor-pointer transition-colors"
          >
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
              {{ hit.snippet }}
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
                {{ hit.matched_chapter.snippet }}
              </p>
            </div>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>
