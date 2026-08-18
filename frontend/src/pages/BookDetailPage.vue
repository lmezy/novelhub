<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useBooksStore, type Book, type Chapter, type CustomTagOnBook, type ShelfGroup } from "../stores/books"
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
interface BookmarkItem {
  id: string
  book_id: string
  chapter_id: string
  position: number
  note?: string | null
  chapter_title?: string | null
  chapter_number?: number | null
}
const bookmarks = ref<BookmarkItem[]>([])
const syncing = ref(false)
const favorite = ref(false)
const alternates = ref<any[]>([])
const showSources = ref(false)
const customTags = ref<CustomTagOnBook[]>([])
const tagDetail = ref<CustomTagOnBook | null>(null)
const newTagName = ref("")
const newTagPublic = ref(false)
const newTagShowUser = ref(true)
const tagSaving = ref(false)
const tagError = ref("")
const coverPickerOpen = ref(false)
const coverError = ref("")
const shelfGroups = ref<ShelfGroup[]>([])
const bookGroupIds = ref<string[]>([])
const groupSaving = ref(false)
const groupError = ref("")
const allCategories = ref<{ id: string; name: string; color?: string | null }[]>([])
const selectedCategoryIds = ref<string[]>([])
const categorySaving = ref(false)
const autoCategorizing = ref(false)
const categoryError = ref("")
const publishing = ref(false)
const convertingToR18 = ref(false)
const editing = ref(false)
const newCategoryName = ref("")
const newCategoryR18 = ref(false)
const categoryCreating = ref(false)

const canPublish = computed(() =>
  auth.isAdmin || book.value?.owner_id === auth.user?.id
)

const showCovers = computed(() => auth.user?.settings?.show_covers !== false)

function searchByField(field: "author" | "tags" | "category", value: string) {
  router.push({ path: "/search", query: { field, q: value } })
}

async function unpublishBook() {
  if (!book.value || !confirm(i18n.t('book_unpublish_confirm'))) return
  publishing.value = true
  try {
    const res = await api.delete<Book>(`/books/${book.value.id}/publish`)
    book.value = {
      ...book.value,
      owner_id: res.owner_id,
      is_public: res.is_public,
      all_ages_confirmed: res.all_ages_confirmed,
    }
    alert(i18n.t('book_unpublish_done'))
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('book_unpublish_failed'))
  } finally {
    publishing.value = false
  }
}

async function convertToR18() {
  if (!book.value || !confirm(i18n.t('book_convert_r18_confirm', { title: book.value.title }))) return
  convertingToR18.value = true
  try {
    const res = await api.post<Book>(`/books/${book.value.id}/to-r18`)
    book.value = {
      ...book.value,
      is_r18: res.is_r18,
      is_public: res.is_public,
      all_ages_confirmed: res.all_ages_confirmed,
    }
    alert(i18n.t('book_convert_r18_done'))
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('book_convert_r18_failed'))
  } finally {
    convertingToR18.value = false
  }
}

function goBack() {
  const back = (window.history.state as { back?: string | null } | null)?.back
  if (back) {
    router.back()
  } else {
    router.push("/books")
  }
}

async function toggleFavorite() {
  if (!book.value) return
  favorite.value = await store.toggleFavorite(book.value)
  await loadBookGroups()
}

async function loadCustomTags() {
  if (!book.value) return
  try {
    customTags.value = await api.get<CustomTagOnBook[]>("/custom-tags/books/" + book.value.id)
  } catch {
    customTags.value = []
  }
}

async function loadCategories() {
  try {
    allCategories.value = await api.get<{ id: string; name: string; color?: string | null }[]>("/categories")
  } catch {
    allCategories.value = []
  }
}

function initCategorySelection() {
  if (!book.value) return
  const names = new Set(book.value.category_names || [])
  selectedCategoryIds.value = allCategories.value
    .filter((cat) => names.has(cat.name))
    .map((cat) => cat.id)
}

async function saveCategories() {
  if (!book.value) return
  categorySaving.value = true
  categoryError.value = ""
  try {
    const res = await api.put<{ id: string; name: string }[]>("/categories/book/" + book.value.id, {
      book_id: book.value.id,
      category_ids: selectedCategoryIds.value,
    })
    book.value.category_names = res.map((cat) => cat.name)
    alert(i18n.t('book_category_saved'))
  } catch (e) {
    categoryError.value = e instanceof Error ? e.message : i18n.t('book_category_failed')
  } finally {
    categorySaving.value = false
  }
}

async function createCategory() {
  const name = newCategoryName.value.trim()
  if (!name) return
  categoryCreating.value = true
  categoryError.value = ""
  try {
    await api.post("/categories", {
      name,
      is_r18: newCategoryR18.value,
    })
    newCategoryName.value = ""
    newCategoryR18.value = false
    await loadCategories()
  } catch (e) {
    categoryError.value = e instanceof Error ? e.message : i18n.t('book_category_create_failed')
  } finally {
    categoryCreating.value = false
  }
}

async function autoCategorizeBook() {
  if (!book.value) return
  autoCategorizing.value = true
  categoryError.value = ""
  try {
    const res = await api.post<{ book_id: string; categories: string[] }>(
      "/categories/auto/" + book.value.id,
    )
    book.value.category_names = res.categories || []
    initCategorySelection()
  } catch (e) {
    categoryError.value = e instanceof Error ? e.message : i18n.t('book_category_failed')
  } finally {
    autoCategorizing.value = false
  }
}

async function addCustomTag() {
  if (!book.value || !newTagName.value.trim()) return
  tagSaving.value = true
  tagError.value = ""
  try {
    customTags.value = await api.post<CustomTagOnBook[]>("/custom-tags/apply", {
      book_id: book.value.id,
      name: newTagName.value.trim(),
      is_public: newTagPublic.value,
      show_user: newTagShowUser.value,
    })
    newTagName.value = ""
  } catch (e) {
    tagError.value = e instanceof Error ? e.message : i18n.t('book_tag_failed')
  } finally {
    tagSaving.value = false
  }
}

async function removeCustomTag(tag: CustomTagOnBook) {
  if (!book.value) return
  tagError.value = ""
  try {
    customTags.value = await api.delete<CustomTagOnBook[]>("/custom-tags/books/" + book.value.id + "/tags/" + tag.id)
  } catch (e) {
    tagError.value = e instanceof Error ? e.message : i18n.t('book_tag_failed')
  }
}

async function removeSourceTag(tag: string) {
  if (!book.value) return
  if (!confirm(i18n.t('book_source_tag_remove_confirm', { tag }))) return
  try {
    await api.delete("/books/" + book.value.id + "/tags?name=" + encodeURIComponent(tag))
    book.value.tag_names = (book.value.tag_names || []).filter((t) => t !== tag)
  } catch (e) {
    tagError.value = e instanceof Error ? e.message : i18n.t('book_tag_failed')
  }
}

async function chooseCover(alt: any) {
  if (!book.value) return
  coverError.value = ""
  try {
    const res = await api.put<{ cover: string | null }>(
      "/books/" + book.value.id + "/cover",
      { source_book_id: alt.id },
    )
    book.value.cover = res.cover
    coverPickerOpen.value = false
  } catch (e) {
    coverError.value = e instanceof Error ? e.message : i18n.t('book_cover_failed')
  }
}

async function resetCover() {
  if (!book.value) return
  coverError.value = ""
  try {
    const res = await api.put<{ cover: string | null }>(
      "/books/" + book.value.id + "/cover",
      {},
    )
    book.value.cover = res.cover
    coverPickerOpen.value = false
  } catch (e) {
    coverError.value = e instanceof Error ? e.message : i18n.t('book_cover_failed')
  }
}

async function loadBookmarks() {
  if (!book.value) return
  try {
    bookmarks.value = await api.get<BookmarkItem[]>("/bookmarks?book_id=" + book.value.id)
  } catch {
    bookmarks.value = []
  }
}

async function removeBookmark(id: string) {
  try {
    await api.delete("/bookmarks/" + id)
    bookmarks.value = bookmarks.value.filter((b) => b.id !== id)
  } catch { /* non-critical */ }
}

function startReading() {
  if (!book.value) return
  const target = savedChapterId.value || (chapters.value.length ? chapters.value[0].id : null)
  if (target) router.push("/books/" + book.value.id + "/chapters/" + target)
}

async function loadShelfGroups() {
  try {
    shelfGroups.value = await api.get<ShelfGroup[]>("/bookshelf/groups")
  } catch {
    shelfGroups.value = []
  }
}

async function loadBookGroups() {
  if (!book.value || !favorite.value) {
    bookGroupIds.value = []
    return
  }
  try {
    const res = await api.get<{ group_ids: string[] }>("/bookshelf/favorites/" + book.value.id + "/groups")
    bookGroupIds.value = res.group_ids || []
  } catch {
    bookGroupIds.value = []
  }
}

async function saveBookGroups() {
  if (!book.value) return
  groupSaving.value = true
  groupError.value = ""
  try {
    await api.put("/bookshelf/favorites/" + book.value.id + "/groups", {
      group_ids: bookGroupIds.value,
    })
    alert(i18n.t('book_group_saved'))
  } catch (e) {
    groupError.value = e instanceof Error ? e.message : i18n.t('book_group_failed')
  } finally {
    groupSaving.value = false
  }
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
    const result = await api.post<any>('/books/' + book.value.id + '/sync')
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
    await loadCustomTags()
    await loadCategories()
    initCategorySelection()
    await loadShelfGroups()
    await loadBookGroups()
    await loadAlternates()
    if (auth.user) {
      try {
        const p = await api.get<any>('/progress/' + route.params.id)
        if (p) savedChapterId.value = p.chapter_id
      } catch { /* non-critical */ }
    }
    await loadBookmarks()
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
        @click="goBack"
        class="text-sm text-muted dark:text-gray-400 hover:text-ink mb-6 inline-flex items-center gap-1 transition-colors"
      >&larr; {{ i18n.t('book_back') }}</button>

      <p v-if="loading" class="text-muted dark:text-gray-400">{{ i18n.t('book_loading') }}</p>
      <p v-else-if="error" class="text-red-600">{{ error }}</p>

      <template v-else-if="book">
        <div class="mb-6 flex flex-wrap items-center gap-2">
          <button
            @click="startReading"
            :disabled="chapters.length === 0"
            class="inline-flex items-center gap-1 px-5 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-40 no-underline"
          >{{ savedChapterId ? i18n.t('book_continue_reading') : i18n.t('book_start_reading') }} &rarr;</button>
        </div>

        <header class="mb-8">
          <div class="mb-4">
            <img
              v-if="showCovers && book.cover"
              :src="book.cover"
              :alt="book.title"
              class="w-40 h-56 object-cover rounded-lg border border-border dark:border-gray-700"
            />
            <button
              v-if="alternates.length > 1"
              @click="coverPickerOpen = true"
              class="mt-2 px-3 py-1 text-xs border border-border dark:border-gray-700 rounded hover:bg-accent/5 transition-colors"
            >{{ i18n.t('book_cover_select') }}</button>
            <p v-if="coverError" class="text-xs text-red-600 mt-1">{{ coverError }}</p>
          </div>
          <h1 class="text-3xl font-bold mb-2">{{ book.title }}</h1>
          <button
            v-if="book.author_name"
            @click="searchByField('author', book.author_name)"
            :title="i18n.t('book_author_search')"
            class="text-muted dark:text-gray-400 mb-1 hover:text-accent transition-colors"
          >{{ book.author_name }}</button>
          <div v-if="book.tag_names?.length" class="flex flex-wrap gap-1 mb-2">
            <template
              v-for="tag in book.tag_names"
              :key="tag"
            >
              <button
                v-if="!editing"
                @click="searchByField('tags', tag)"
                :title="i18n.t('book_tag_search')"
                class="text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-muted dark:text-gray-400 hover:text-accent transition-colors"
              >{{ tag }}</button>
              <span
                v-else
                class="inline-flex items-center text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-muted dark:text-gray-400"
              >
                {{ tag }}
                <button
                  v-if="auth.isAdmin"
                  @click="removeSourceTag(tag)"
                  class="ml-1 text-red-400 hover:text-red-600"
                  :title="i18n.t('book_source_tag_remove')"
                >&times;</button>
              </span>
            </template>
          </div>
          <div v-if="book.category_names?.length" class="flex flex-wrap gap-1 mb-2">
            <template
              v-for="cat in book.category_names"
              :key="cat"
            >
              <button
                v-if="!editing"
                @click="searchByField('category', cat)"
                :title="i18n.t('book_category_search')"
                class="text-xs px-2 py-0.5 rounded bg-accent/10 text-accent hover:bg-accent/20 transition-colors"
              >{{ cat }}</button>
              <span v-else class="text-xs px-2 py-0.5 rounded bg-accent/10 text-accent">{{ cat }}</span>
            </template>
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
            >{{ i18n.t('book_switch_source') }} ({{ alternates.length }})</button>
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
              v-if="canPublish && !book.is_r18"
              @click="convertToR18"
              :disabled="convertingToR18"
              class="px-3 py-1 text-xs border border-purple-500 text-purple-700 dark:text-purple-400 rounded hover:bg-purple-50 dark:hover:bg-purple-950 disabled:opacity-50"
            >{{ convertingToR18 ? i18n.t('book_converting_r18') : i18n.t('book_convert_r18') }}</button>
            <button
              v-if="canPublish && book.is_public"
              @click="unpublishBook"
              :disabled="publishing"
              class="px-3 py-1 text-xs border border-border dark:border-gray-700 rounded hover:bg-accent/5 disabled:opacity-50"
            >{{ i18n.t('book_unpublish') }}</button>
            <span
              v-if="book.is_public && book.all_ages_confirmed"
              class="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
            >{{ i18n.t('book_all_ages_confirmed') }}</span>
            <button
              @click="toggleFavorite"
              class="px-3 py-1 text-xs border border-border dark:border-gray-700 rounded hover:bg-accent/5 transition-colors"
            >{{ favorite ? i18n.t('book_favorite_added') : i18n.t('book_favorite') }}</button>
            <button
              @click="editing = !editing"
              class="px-3 py-1 text-xs border border-accent/50 text-accent rounded hover:bg-accent/5 transition-colors"
            >{{ editing ? i18n.t('book_edit_done') : i18n.t('book_edit') }}</button>
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

        <section v-if="auth.isAdmin && editing" class="mb-8">
          <h2 class="text-lg font-semibold mb-3">{{ i18n.t('book_category_manage') }}</h2>
          <div v-if="allCategories.length" class="flex flex-wrap gap-3 mb-3">
            <label
              v-for="cat in allCategories"
              :key="cat.id"
              class="inline-flex items-center gap-1.5 text-sm cursor-pointer"
            >
              <input type="checkbox" :value="cat.id" v-model="selectedCategoryIds" class="rounded" />
              <span>{{ cat.name }}</span>
            </label>
          </div>
          <p v-else class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('book_category_empty') }}</p>
          <div class="flex flex-wrap items-center gap-2 mb-3">
            <input
              v-model="newCategoryName"
              :placeholder="i18n.t('book_category_name_placeholder')"
              @keyup.enter="createCategory"
              class="w-36 px-2 py-1.5 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800"
            />
            <label class="inline-flex items-center gap-1 text-xs text-muted dark:text-gray-400 cursor-pointer">
              <input type="checkbox" v-model="newCategoryR18" class="rounded" />
              {{ i18n.t('book_category_r18') }}
            </label>
            <button
              @click="createCategory"
              :disabled="categoryCreating"
              class="px-3 py-1.5 rounded border border-accent/40 text-accent text-xs font-medium hover:bg-accent/5 disabled:opacity-50"
            >{{ i18n.t('book_category_add') }}</button>
          </div>
          <button
            @click="saveCategories"
            :disabled="categorySaving"
            class="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
          >{{ i18n.t('book_category_save') }}</button>
          <button
            @click="autoCategorizeBook"
            :disabled="autoCategorizing"
            class="px-3 py-1.5 rounded border border-accent/40 text-accent text-xs font-medium hover:bg-accent/5 disabled:opacity-50"
          >{{ autoCategorizing ? i18n.t('book_category_auto_running') : i18n.t('book_category_auto') }}</button>
          <p v-if="categoryError" class="text-xs text-red-600 mt-2">{{ categoryError }}</p>
        </section>

        <section class="mb-8">
          <h2 class="text-lg font-semibold mb-3">{{ i18n.t('book_custom_tags') }}</h2>
          <div v-if="customTags.length" class="flex flex-wrap gap-2 mb-3">
            <template
              v-for="tag in customTags"
              :key="tag.id"
            >
              <button
                v-if="!editing"
                @click="searchByField('tags', tag.name)"
                :title="i18n.t('book_tag_search')"
                class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-amber-100 dark:bg-amber-900/60 text-amber-700 dark:text-amber-300 hover:opacity-80 transition-opacity"
              >
                {{ tag.name }} ×{{ tag.count }}
                <span
                  v-if="tag.is_public"
                  @click.stop="tagDetail = tag"
                  class="text-accent cursor-pointer"
                  :title="i18n.t('book_tag_detail')"
                >ℹ</span>
              </button>
              <span
                v-else
                class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-amber-100 dark:bg-amber-900/60 text-amber-700 dark:text-amber-300"
              >
                {{ tag.name }} ×{{ tag.count }}
                <span
                  v-if="tag.applied_by_me"
                  @click="removeCustomTag(tag)"
                  class="text-red-500 hover:text-red-700 cursor-pointer"
                  :title="i18n.t('book_tag_remove')"
                >&times;</span>
              </span>
            </template>
          </div>
          <p v-else class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('book_custom_tags_empty') }}</p>
          <div class="flex flex-wrap items-center gap-2">
            <input
              v-model="newTagName"
              :placeholder="i18n.t('book_tag_name_placeholder')"
              @keyup.enter="addCustomTag"
              class="w-36 px-3 py-1.5 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
            />
            <label class="inline-flex items-center gap-1 text-xs text-muted dark:text-gray-400 cursor-pointer">
              <input type="checkbox" v-model="newTagPublic" class="rounded" />
              {{ i18n.t('book_tag_public') }}
            </label>
            <label v-if="newTagPublic" class="inline-flex items-center gap-1 text-xs text-muted dark:text-gray-400 cursor-pointer">
              <input type="checkbox" v-model="newTagShowUser" class="rounded" />
              {{ i18n.t('book_tag_show_user') }}
            </label>
            <button
              @click="addCustomTag"
              :disabled="tagSaving"
              class="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
            >{{ i18n.t('book_tag_add') }}</button>
          </div>
          <p v-if="tagError" class="text-xs text-red-600 mt-2">{{ tagError }}</p>
        </section>

        <section v-if="favorite" class="mb-8">
          <h2 class="text-lg font-semibold mb-3">{{ i18n.t('book_shelf_groups') }}</h2>
          <div v-if="shelfGroups.length" class="flex flex-wrap gap-3 mb-3">
            <label v-for="g in shelfGroups" :key="g.id" class="inline-flex items-center gap-1.5 text-sm cursor-pointer">
              <input type="checkbox" :value="g.id" v-model="bookGroupIds" class="rounded" />
              <span>{{ g.name }}</span>
            </label>
          </div>
          <p v-else class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('home_group_empty') }}</p>
          <button
            @click="saveBookGroups"
            :disabled="groupSaving"
            class="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
          >{{ i18n.t('book_group_save') }}</button>
          <p v-if="groupError" class="text-xs text-red-600 mt-2">{{ groupError }}</p>
        </section>

        <section class="mb-8">
          <h2 class="text-lg font-semibold mb-3">{{ i18n.t('book_bookmarks_title') }} ({{ bookmarks.length }})</h2>
          <p v-if="bookmarks.length === 0" class="text-xs text-muted dark:text-gray-400">{{ i18n.t('book_bookmarks_empty') }}</p>
          <div v-else class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
            <div v-for="bm in bookmarks" :key="bm.id" class="flex items-center gap-2 px-4 py-2.5">
              <router-link
                :to="'/books/' + book.id + '/chapters/' + bm.chapter_id"
                class="min-w-0 flex-1 no-underline"
              >
                <span class="block truncate text-sm hover:text-accent">
                  {{ bm.chapter_title || i18n.t('reader_bookmark_chapter', { n: bm.chapter_number || '' }) }}
                </span>
                <span class="block text-xs text-muted dark:text-gray-400">{{ i18n.t('reader_bookmark_position', { p: bm.position }) }}</span>
              </router-link>
              <button
                @click="removeBookmark(bm.id)"
                class="shrink-0 text-xs text-red-400 hover:text-red-600 px-1.5"
                :title="i18n.t('reader_bookmark_delete')"
              >&times;</button>
            </div>
          </div>
        </section>

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

      <div v-if="coverPickerOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="coverPickerOpen = false">
        <div class="w-full max-w-lg rounded-lg border border-border dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
          <h3 class="text-sm font-semibold mb-3">{{ i18n.t('book_cover_choose') }}</h3>
          <div class="grid grid-cols-3 gap-3">
            <button
              v-for="alt in alternates"
              :key="alt.id"
              @click="chooseCover(alt)"
              class="flex flex-col items-center gap-1 p-2 rounded border border-border dark:border-gray-700 hover:border-accent/40 hover:bg-accent/5 transition-colors"
            >
              <img
                v-if="alt.cover"
                :src="alt.cover"
                :alt="alt.title"
                class="h-32 w-24 object-cover rounded border border-border dark:border-gray-700"
              />
              <span
                v-else
                class="h-32 w-24 flex items-center justify-center text-xs text-muted dark:text-gray-400 rounded border border-border dark:border-gray-700"
              >{{ i18n.t('book_cover_none') }}</span>
              <span class="text-xs text-muted dark:text-gray-400 truncate w-full text-center">
                {{ alt.source_name || alt.id }}<template v-if="alt.is_current"> · {{ i18n.t('book_cover_current') }}</template>
              </span>
            </button>
          </div>
          <div class="mt-4 flex items-center gap-2">
            <button
              @click="resetCover"
              class="px-3 py-1.5 rounded border border-border dark:border-gray-700 text-xs"
            >{{ i18n.t('book_cover_reset') }}</button>
            <button
              @click="coverPickerOpen = false"
              class="px-3 py-1.5 rounded border border-border dark:border-gray-700 text-xs"
            >{{ i18n.t('home_close') }}</button>
          </div>
        </div>
      </div>

      <div v-if="tagDetail" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="tagDetail = null">
        <div class="w-full max-w-sm rounded-lg border border-border dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
          <h3 class="text-sm font-semibold mb-1">{{ tagDetail.name }}</h3>
          <p class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('book_tag_count', { n: tagDetail.count }) }}</p>
          <ul v-if="tagDetail.show_user && tagDetail.users.length" class="space-y-1 text-sm max-h-48 overflow-y-auto">
            <li v-for="u in tagDetail.users" :key="u.id">{{ u.username }}</li>
          </ul>
          <p v-else class="text-xs text-muted dark:text-gray-400">{{ i18n.t('book_tag_users_hidden') }}</p>
          <button
            @click="tagDetail = null"
            class="mt-4 px-3 py-1.5 rounded border border-border dark:border-gray-700 text-xs"
          >{{ i18n.t('home_close') }}</button>
        </div>
      </div>
    </main>
  </div>
</template>