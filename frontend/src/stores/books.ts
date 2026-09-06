import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"

export interface Book {
  id: string
  title: string
  source_id: string | null
  author_id: string | null
  source_book_id: string | null
  description: string | null
  cover: string | null
  status: string | null
  is_r18?: boolean
  is_favorite?: boolean
  owner_id?: string | null
  is_public?: boolean
  all_ages_confirmed?: boolean
  created_at: string
  updated_at: string
  tag_names: string[]
  category_names: string[]
  author_name: string | null
  custom_tags?: CustomTagOnBook[]
  shelf_group_ids?: string[]
}

export interface Chapter {
  id: string
  book_id: string
  chapter_number: number
  source_chapter_id: string | null
  title: string | null
  created_at: string
}

export interface ChapterContent extends Chapter {
  content: string
}

export interface CustomTagUser {
  id: string
  username: string
}

export interface CustomTagOnBook {
  id: string
  name: string
  is_public: boolean
  show_user: boolean
  count: number
  applied_by_me: boolean
  users: CustomTagUser[]
}

// Keep recently opened chapters in the browser process and prefetch the next
// chapter. This avoids making every page turn wait for the API/storage read.
const chapterCache = new Map<string, ChapterContent>()

export interface ShelfGroup {
  id: string
  name: string
  order: number
  show: boolean
  count: number
}

export const useBooksStore = defineStore("books", () => {
  const books = ref<Book[]>([])
  const loading = ref(false)
  const error = ref("")

  async function fetchBooks() {
    loading.value = true
    error.value = ""
    try {
      books.value = await api.get<Book[]>("/books")
    } catch (e) {
      error.value = e instanceof Error ? e.message : "Failed to load books"
    } finally {
      loading.value = false
    }
  }

  async function fetchBook(id: string): Promise<Book> {
    return api.get<Book>(`/books/${id}`)
  }

  async function fetchChapters(bookId: string): Promise<Chapter[]> {
    return api.get<Chapter[]>(`/books/${bookId}/chapters`)
  }

  async function fetchChapter(chapterId: string): Promise<ChapterContent> {
    const cached = chapterCache.get(chapterId)
    if (cached) return cached
    const loaded = await api.get<ChapterContent>(`/chapters/${chapterId}`)
    chapterCache.set(chapterId, loaded)
    return loaded
  }

  async function prefetchChapter(chapterId: string): Promise<void> {
    if (chapterCache.has(chapterId)) return
    try {
      const loaded = await api.get<ChapterContent>(`/chapters/${chapterId}`)
      chapterCache.set(chapterId, loaded)
    } catch {
      // Prefetch is best effort; the normal navigation request reports errors.
    }
  }

  function invalidateChapter(chapterId: string): void {
    chapterCache.delete(chapterId)
  }

  async function fetchFavorites(groupId?: string): Promise<Book[]> {
    const query = groupId ? `?group_id=${encodeURIComponent(groupId)}` : ""
    return api.get<Book[]>(`/books/favorites${query}`)
  }

  async function toggleFavorite(book: Book): Promise<boolean> {
    const next = !book.is_favorite
    if (next) {
      await api.post(`/books/${book.id}/favorite`)
    } else {
      await api.delete(`/books/${book.id}/favorite`)
    }
    book.is_favorite = next
    return next
  }

  return { books, loading, error, fetchBooks, fetchBook, fetchChapters, fetchChapter, prefetchChapter, invalidateChapter, fetchFavorites, toggleFavorite }
})
