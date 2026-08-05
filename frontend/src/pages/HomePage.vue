<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useBooksStore, type Book } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"

const store = useBooksStore()
const auth = useAuthStore()
const i18n = useI18nStore()
const recentReads = ref<Book[]>([])
const favoriteBooks = ref<Book[]>([])
const favoriteLoading = ref(false)
const favoriteError = ref("")
const selectedIds = ref<string[]>([])
const batchDeleting = ref(false)

async function loadFavorites() {
  favoriteLoading.value = true
  favoriteError.value = ""
  try {
    favoriteBooks.value = await store.fetchFavorites()
  } catch (e) {
    favoriteError.value = e instanceof Error ? e.message : i18n.t('home_load_favorites_failed')
  } finally {
    favoriteLoading.value = false
  }
}

async function toggleFavorite(book: Book) {
  const isFavorite = await store.toggleFavorite(book)
  if (!isFavorite) {
    favoriteBooks.value = favoriteBooks.value.filter((b) => b.id !== book.id)
  }
}

async function deleteBook(id: string, title: string) {
  if (!confirm(i18n.t('home_delete_confirm', { title }))) return
  try {
    await api.delete('/books/' + id)
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
    await loadFavorites()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_delete_failed'))
  }
}

function toggleSelect(id: string) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((x) => x !== id)
    : [...selectedIds.value, id]
}

async function batchDelete() {
  if (!selectedIds.value.length) return
  if (!confirm(i18n.t('home_batch_delete_confirm', { n: selectedIds.value.length }))) return
  batchDeleting.value = true
  try {
    await api.post('/books/batch-delete', { ids: selectedIds.value })
    selectedIds.value = []
    await loadFavorites()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_batch_delete_failed'))
  } finally {
    batchDeleting.value = false
  }
}

async function toggleSelfVisibility(key: "r18_enabled" | "non_r18_enabled") {
  if (!auth.user) return
  try {
    await auth.updateVisibility({ [key]: !auth.user[key] })
    await loadFavorites()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_update_failed'))
  }
}

onMounted(async () => {
  await loadFavorites()
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
  <div class="min-h-screen bg-paper dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-5xl mx-auto px-4 py-8">
      <section v-if="recentReads.length > 0" class="mb-10">
        <h2 class="text-lg font-semibold mb-3">{{ i18n.t('home_continue') }}</h2>
        <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <router-link
            v-for="b in recentReads.slice(0, 4)"
            :key="b.id"
            :to="'/books/' + b.id"
            class="p-3 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 hover:shadow-sm hover:border-accent/30 transition-all no-underline"
          >
            <p class="text-sm font-medium truncate">{{ b.title }}</p>
            <p class="text-xs text-muted dark:text-gray-400 mt-1">{{ b.status || i18n.t('home_reading') }}</p>
          </router-link>
        </div>
      </section>

      <section>
        <div class="flex items-center justify-between mb-6">
          <h1 class="text-2xl font-bold tracking-tight">{{ i18n.t('home_library') }}</h1>
          <div class="flex items-center gap-3">
            <template v-if="auth.user?.can_manage_visibility && !auth.isAdmin">
              <button
                @click="toggleSelfVisibility('r18_enabled')"
                class="text-xs px-3 py-1.5 rounded border text-sm"
                :class="auth.user.r18_enabled ? 'bg-purple-100 text-purple-700 border-purple-400 dark:bg-purple-900 dark:text-purple-300' : 'border-border dark:border-gray-700 text-muted dark:text-gray-400'"
              >{{ auth.user.r18_enabled ? i18n.t('home_r18_on') : i18n.t('home_r18_off') }}</button>
              <button
                @click="toggleSelfVisibility('non_r18_enabled')"
                class="text-xs px-3 py-1.5 rounded border text-sm"
                :class="auth.user.non_r18_enabled ? 'bg-green-100 text-green-700 border-green-400 dark:bg-green-900 dark:text-green-300' : 'border-border dark:border-gray-700 text-muted dark:text-gray-400'"
              >{{ auth.user.non_r18_enabled ? i18n.t('home_all_ages_on') : i18n.t('home_all_ages_off') }}</button>
            </template>
            <span class="text-sm text-muted dark:text-gray-400">{{ i18n.t('home_books_count', { n: favoriteBooks.length }) }}</span>
            <button
              v-if="auth.isAdmin && selectedIds.length"
              @click="batchDelete"
              :disabled="batchDeleting"
              class="text-xs px-3 py-1.5 rounded bg-red-500 text-white hover:bg-red-600 disabled:opacity-50"
            >{{ i18n.t('books_batch_delete') }} ({{ selectedIds.length }})</button>
          </div>
        </div>

        <p v-if="favoriteLoading" class="text-muted dark:text-gray-400">{{ i18n.t('home_loading') }}</p>
        <p v-else-if="favoriteError" class="text-red-600">{{ favoriteError }}</p>

        <div v-else-if="favoriteBooks.length === 0" class="text-center py-16">
          <p class="text-muted dark:text-gray-400 text-lg mb-2">{{ i18n.t('home_shelf_empty') }}</p>
          <p class="text-sm text-muted dark:text-gray-400">{{ i18n.t('home_shelf_empty_hint') }}</p>
          <router-link
            to="/books"
            class="inline-block mt-4 text-sm text-accent hover:underline"
          >{{ i18n.t('home_goto_books') }}</router-link>
        </div>

        <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <router-link
            v-for="book in favoriteBooks"
            :key="book.id"
            :to="'/books/' + book.id"
            class="relative group block p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 hover:shadow-md hover:border-accent/30 transition-all duration-200 no-underline"
          >
            <input
              v-if="auth.isAdmin"
              type="checkbox"
              :checked="selectedIds.includes(book.id)"
              @click.stop="toggleSelect(book.id)"
              class="absolute top-2 left-2 w-4 h-4 rounded border-border"
              :title="i18n.t('home_delete_title')"
            />
            <button
              @click.prevent.stop="toggleFavorite(book)"
              class="absolute top-2 right-2 w-7 h-7 flex items-center justify-center rounded text-amber-500 text-base"
              :title="i18n.t('home_favorite_remove')"
            >★</button>
            <button
              v-if="auth.isAdmin"
              @click.prevent.stop="deleteBook(book.id, book.title)"
              class="absolute top-2 right-10 text-xs text-red-400 hover:text-red-600 opacity-0 group-hover:opacity-100 transition-opacity"
              :title="i18n.t('home_delete_title')"
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
      </section>
    </main>
  </div>
</template>
