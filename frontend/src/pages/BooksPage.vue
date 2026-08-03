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
const selectedIds = ref<string[]>([])
const batchDeleting = ref(false)

function toggleSelect(id: string) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((x) => x !== id)
    : [...selectedIds.value, id]
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
  if (!confirm(`确定删除选中的 ${selectedIds.value.length} 本书吗？此操作不可撤销。`)) return
  batchDeleting.value = true
  try {
    await api.post('/books/batch-delete', { ids: selectedIds.value })
    selectedIds.value = []
    await store.fetchBooks()
  } catch (e) {
    alert(e instanceof Error ? e.message : '批量删除失败')
  } finally {
    batchDeleting.value = false
  }
}

onMounted(async () => {
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
            <h1 class="text-2xl font-bold">全部书籍</h1>
            <p class="text-sm text-muted dark:text-gray-400 mt-1">仓库中已经保存的所有小说</p>
          </div>
          <div class="flex items-center gap-3">
            <span class="text-sm text-muted dark:text-gray-400">{{ i18n.t('home_books_count', { n: store.books.length }) }}</span>
            <button
              v-if="auth.isAdmin && selectedIds.length"
              @click="batchDelete"
              :disabled="batchDeleting"
              class="text-xs px-3 py-1.5 rounded bg-red-500 text-white hover:bg-red-600 disabled:opacity-50"
            >批量删除 ({{ selectedIds.length }})</button>
          </div>
        </div>

        <p v-if="store.loading" class="text-muted dark:text-gray-400">{{ i18n.t('home_loading') }}</p>
        <p v-else-if="store.error" class="text-red-600">{{ store.error }}</p>

        <div v-else-if="store.books.length === 0" class="text-center py-16">
          <p class="text-muted dark:text-gray-400 text-lg mb-2">仓库还没有书籍</p>
          <router-link to="/admin" class="inline-block mt-4 text-sm text-accent hover:underline">前往管理页同步</router-link>
        </div>

        <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <router-link
            v-for="book in store.books"
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
            />
            <button
              @click.prevent.stop="toggleFavorite(book)"
              class="absolute top-2 right-2 w-7 h-7 flex items-center justify-center rounded text-base"
              :class="book.is_favorite ? 'text-amber-500' : 'text-muted hover:text-amber-500'"
              :title="book.is_favorite ? '取消收藏' : '收藏到书架'"
            >{{ book.is_favorite ? '★' : '☆' }}</button>
            <h3 class="font-semibold text-ink mb-1 truncate pr-6">{{ book.title }}</h3>
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
