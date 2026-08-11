<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { useBooksStore, type Book } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"

const store = useBooksStore()
const auth = useAuthStore()
const i18n = useI18nStore()
const router = useRouter()
const selectedIds = ref<string[]>([])
const batchDeleting = ref(false)
const batchFavoriting = ref(false)
const autoCategorizing = ref(false)
const categories = ref<{ id: string; name: string; color?: string | null }[]>([])
const selectedCategory = ref("")
const newCategoryName = ref("")
const newCategoryR18 = ref(false)
const categoryCreating = ref(false)
const categoryError = ref("")

const filteredBooks = computed(() => {
  if (!selectedCategory.value) return store.books
  return store.books.filter((book) => book.category_names?.includes(selectedCategory.value))
})

const allSelected = computed(() =>
  filteredBooks.value.length > 0 &&
  filteredBooks.value.every((book) => selectedIds.value.includes(book.id))
)

function toggleSelect(id: string) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((x) => x !== id)
    : [...selectedIds.value, id]
}

function selectAll() {
  selectedIds.value = filteredBooks.value.map((book) => book.id)
}

function invertSelection() {
  const selected = new Set(selectedIds.value)
  selectedIds.value = filteredBooks.value
    .filter((book) => !selected.has(book.id))
    .map((book) => book.id)
}

function clearSelection() {
  selectedIds.value = []
}

async function toggleFavorite(book: Book) {
  await store.toggleFavorite(book)
}

async function deleteBook(id: string, title: string) {
  if (!confirm(i18n.t('home_delete_confirm', { title }))) return
  try {
    await api.delete('/books/' + id)
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
    await store.fetchBooks()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_delete_failed'))
  }
}

async function batchDelete() {
  if (!selectedIds.value.length) return
  if (!confirm(i18n.t('books_batch_delete_confirm', { n: selectedIds.value.length }))) return
  batchDeleting.value = true
  try {
    await api.post('/books/batch-delete', { ids: selectedIds.value })
    selectedIds.value = []
    await store.fetchBooks()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('books_batch_delete_failed'))
  } finally {
    batchDeleting.value = false
  }
}

async function batchAddShelf() {
  if (!selectedIds.value.length) return
  batchFavoriting.value = true
  try {
    const res = await api.post('/books/batch-favorite', { ids: selectedIds.value }) as { added: number }
    alert(i18n.t('books_batch_add_shelf_done', { n: res.added }))
    selectedIds.value = []
    await store.fetchBooks()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('books_batch_add_shelf_failed'))
  } finally {
    batchFavoriting.value = false
  }
}

async function autoCategorizeAll() {
  if (!confirm(i18n.t('books_auto_category_confirm'))) return
  autoCategorizing.value = true
  try {
    const res = await api.post<{ total: number; categorized: number }>("/categories/auto")
    alert(i18n.t('books_auto_category_done', { total: res.total, categorized: res.categorized }))
    await store.fetchBooks()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('books_auto_category_failed'))
  } finally {
    autoCategorizing.value = false
  }
}

async function loadCategories() {
  try {
    categories.value = await api.get<{ id: string; name: string; color?: string | null }[]>("/categories")
  } catch {
    categories.value = []
  }
}

function searchByField(field: "author" | "tags" | "category", value: string) {
  router.push({ path: "/search", query: { field, q: value } })
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

onMounted(async () => {
  await loadCategories()
  await store.fetchBooks()
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-5xl mx-auto px-4 py-8">
      <section>
        <div class="flex items-center justify-between mb-6">
          <div>
            <h1 class="text-2xl font-bold">{{ i18n.t('books_title') }}</h1>
            <p class="text-sm text-muted dark:text-gray-400 mt-1">{{ i18n.t('books_subtitle') }}</p>
          </div>
          <div class="flex items-center gap-3">
            <span class="text-sm text-muted dark:text-gray-400">{{ i18n.t('home_books_count', { n: filteredBooks.length }) }}</span>
            <button
              v-if="auth.isAdmin"
              @click="autoCategorizeAll"
              :disabled="autoCategorizing"
              class="text-xs px-3 py-1.5 rounded border border-accent/40 text-accent hover:bg-accent/5 disabled:opacity-50"
            >{{ autoCategorizing ? i18n.t('books_auto_category_running') : i18n.t('books_auto_category') }}</button>
            <button
              v-if="selectedIds.length"
              @click="batchAddShelf"
              :disabled="batchFavoriting"
              class="text-xs px-3 py-1.5 rounded bg-accent text-white hover:opacity-90 disabled:opacity-50"
            >{{ i18n.t('books_batch_add_shelf') }} ({{ selectedIds.length }})</button>
            <button
              v-if="auth.isAdmin && selectedIds.length"
              @click="batchDelete"
              :disabled="batchDeleting"
              class="text-xs px-3 py-1.5 rounded bg-red-500 text-white hover:bg-red-600 disabled:opacity-50"
            >{{ i18n.t('books_batch_delete') }} ({{ selectedIds.length }})</button>
          </div>
        </div>

        <p v-if="store.loading" class="text-muted dark:text-gray-400">{{ i18n.t('home_loading') }}</p>
          <p v-else-if="store.error" class="text-red-600">{{ store.error }}</p>

        <div v-else-if="store.books.length === 0" class="text-center py-16">
          <p class="text-muted dark:text-gray-400 text-lg mb-2">{{ i18n.t('books_empty') }}</p>
          <router-link to="/admin" class="inline-block mt-4 text-sm text-accent hover:underline">{{ i18n.t('books_empty_hint') }}</router-link>
        </div>

        <template v-else>
          <div class="mb-3 flex flex-wrap items-center gap-2 text-xs">
            <label class="inline-flex items-center gap-1.5 cursor-pointer select-none">
              <span>{{ i18n.t('books_category_label') }}</span>
              <select
                v-model="selectedCategory"
                class="px-2 py-1 rounded border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-xs focus:outline-none"
              >
                <option value="">{{ i18n.t('books_all_categories') }}</option>
                <option v-for="cat in categories" :key="cat.id" :value="cat.name">{{ cat.name }}</option>
              </select>
            </label>
            <template v-if="auth.isAdmin">
              <span class="inline-flex items-center gap-1">
                <input
                  v-model="newCategoryName"
                  :placeholder="i18n.t('book_category_name_placeholder')"
                  @keyup.enter="createCategory"
                  class="w-28 px-2 py-1 rounded border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-xs"
                />
                <label class="inline-flex items-center gap-1 text-muted dark:text-gray-400 cursor-pointer">
                  <input type="checkbox" v-model="newCategoryR18" class="rounded" />
                  {{ i18n.t('book_category_r18') }}
                </label>
                <button
                  @click="createCategory"
                  :disabled="categoryCreating"
                  class="px-2 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
                >{{ i18n.t('book_category_add') }}</button>
              </span>
              <span v-if="categoryError" class="text-red-600">{{ categoryError }}</span>
            </template>
            <label class="inline-flex items-center gap-1.5 cursor-pointer select-none">
              <input
                type="checkbox"
                :checked="allSelected"
                @change="allSelected ? clearSelection() : selectAll()"
                class="rounded"
              />
              <span>{{ i18n.t('books_select_all') }}</span>
            </label>
            <button
              type="button"
              @click="invertSelection"
              class="px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5"
            >{{ i18n.t('books_select_invert') }}</button>
            <button
              type="button"
              @click="clearSelection"
              class="px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5"
            >{{ i18n.t('books_select_none') }}</button>
            <span class="text-muted dark:text-gray-400">{{ i18n.t('books_selected_count', { n: selectedIds.length }) }}</span>
          </div>

          <p v-if="filteredBooks.length === 0" class="text-muted dark:text-gray-400 text-center py-10">
            {{ i18n.t('books_category_empty') }}
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <router-link
            v-for="book in filteredBooks"
            :key="book.id"
            :to="'/books/' + book.id"
            class="relative group block p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 hover:shadow-md hover:border-accent/30 transition-all duration-200 no-underline"
          >
            <input
              type="checkbox"
              :checked="selectedIds.includes(book.id)"
              @click.stop="toggleSelect(book.id)"
              class="absolute top-2 left-2 w-4 h-4 rounded border-border"
            />
            <button
              @click.prevent.stop="toggleFavorite(book)"
              class="absolute top-2 right-2 w-7 h-7 flex items-center justify-center rounded text-base"
              :class="book.is_favorite ? 'text-amber-500' : 'text-muted hover:text-amber-500'"
              :title="book.is_favorite ? i18n.t('books_favorite_on') : i18n.t('books_favorite_off')"
            >{{ book.is_favorite ? '★' : '☆' }}</button>
            <img
              v-if="book.cover"
              :src="book.cover"
              :alt="book.title"
              class="w-full h-44 object-cover rounded-md mb-3 border border-border dark:border-gray-700"
            />
            <h3 class="font-semibold text-ink mb-1 truncate pr-6">{{ book.title }}</h3>
            <button
              v-if="book.author_name"
              @click.prevent.stop="searchByField('author', book.author_name)"
              :title="i18n.t('book_author_search')"
              class="text-xs text-muted dark:text-gray-400 mb-1 hover:text-accent transition-colors"
            >{{ book.author_name }}</button>
            <div v-if="book.category_names?.length" class="flex flex-wrap gap-1 mb-1">
              <button
                v-for="cat in book.category_names"
                :key="cat"
                @click.prevent.stop="searchByField('category', cat)"
                :title="i18n.t('book_category_search')"
                class="text-xs px-2 py-0.5 rounded bg-accent/10 text-accent"
              >{{ cat }}</button>
            </div>
            <div v-if="book.tag_names?.length || book.custom_tags?.length" class="flex flex-wrap gap-1 mb-2">
              <button
                v-for="tag in book.tag_names"
                :key="tag"
                @click.prevent.stop="searchByField('tags', tag)"
                :title="i18n.t('book_tag_search')"
                class="text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-muted dark:text-gray-400"
              >{{ tag }}</button>
              <button
                v-for="tag in book.custom_tags || []"
                :key="tag.id"
                @click.prevent.stop="searchByField('tags', tag.name)"
                :title="i18n.t('book_tag_search')"
                class="text-xs px-2 py-0.5 rounded bg-amber-100 dark:bg-amber-900/60 text-amber-700 dark:text-amber-300"
              >{{ tag.name }}<template v-if="tag.count > 1"> ×{{ tag.count }}</template></button>
            </div>
            <p class="text-sm text-muted dark:text-gray-400 line-clamp-2 mb-3">
              {{ book.description || i18n.t('home_no_desc') }}
            </p>
            <div class="flex items-center gap-2">
              <span
                class="text-xs px-2 py-0.5 rounded-full"
                :class="book.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'"
              >{{ book.status === 'completed' ? i18n.t('home_completed') : book.status || i18n.t('home_ongoing') }}</span>
              <span class="text-xs text-muted dark:text-gray-400 ml-auto">
                {{ new Date(book.updated_at).toLocaleDateString() }}
              </span>
            </div>
          </router-link>
          </div>
        </template>
      </section>
    </main>
  </div>
</template>
