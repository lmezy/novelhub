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

export interface ChapterContentChunk extends ChapterContent {
  offset: number
  next_offset: number | null
  total_length: number
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

// Keep recently opened chapters in memory and IndexedDB. IndexedDB is used
// because a novel chapter can exceed localStorage's quota.
const chapterCache = new Map<string, ChapterContent>()
const CHAPTER_CACHE_DB = "novelhub-reader-cache"
const CHAPTER_CACHE_STORE = "chapters"

function openChapterCache(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null)
  return new Promise((resolve) => {
    const request = indexedDB.open(CHAPTER_CACHE_DB, 1)
    request.onupgradeneeded = () => {
      request.result.createObjectStore(CHAPTER_CACHE_STORE, { keyPath: "key" })
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => resolve(null)
  })
}

async function readCachedChapter(key: string): Promise<any | null> {
  const db = await openChapterCache()
  if (!db) return null
  return new Promise((resolve) => {
    const tx = db.transaction(CHAPTER_CACHE_STORE, "readonly")
    const request = tx.objectStore(CHAPTER_CACHE_STORE).get(key)
    request.onsuccess = () => resolve(request.result?.value || null)
    request.onerror = () => resolve(null)
  })
}

async function writeCachedChapter(key: string, value: any): Promise<void> {
  const db = await openChapterCache()
  if (!db) return
  await new Promise<void>((resolve) => {
    const tx = db.transaction(CHAPTER_CACHE_STORE, "readwrite")
    tx.objectStore(CHAPTER_CACHE_STORE).put({ key, value })
    tx.oncomplete = () => resolve()
    tx.onerror = () => resolve()
    tx.onabort = () => resolve()
  })
}

async function deleteCachedChapter(chapterId: string): Promise<void> {
  const db = await openChapterCache()
  if (!db) return
  await new Promise<void>((resolve) => {
    const tx = db.transaction(CHAPTER_CACHE_STORE, "readwrite")
    const store = tx.objectStore(CHAPTER_CACHE_STORE)
    store.delete(`chapter:${chapterId}`)
    const cursor = store.openCursor()
    cursor.onsuccess = () => {
      const current = cursor.result
      if (!current) return
      if (String(current.key).startsWith(`chunk:${chapterId}:`)) current.delete()
      current.continue()
    }
    tx.oncomplete = () => resolve()
    tx.onerror = () => resolve()
    tx.onabort = () => resolve()
  })
}

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
    const persistent = await readCachedChapter(`chapter:${chapterId}`) as ChapterContent | null
    if (persistent) {
      chapterCache.set(chapterId, persistent)
      return persistent
    }
    const loaded = await api.get<ChapterContent>(`/chapters/${chapterId}`)
    chapterCache.set(chapterId, loaded)
    void writeCachedChapter(`chapter:${chapterId}`, loaded)
    return loaded
  }

  async function fetchChapterChunk(
    chapterId: string,
    offset = 0,
    limit = 200_000,
  ): Promise<ChapterContentChunk> {
    const key = `chunk:${chapterId}:${offset}:${limit}`
    const persistent = await readCachedChapter(key) as ChapterContentChunk | null
    if (persistent) return persistent
    const loaded = await api.get<ChapterContentChunk>(
      `/chapters/${chapterId}/content?offset=${offset}&limit=${limit}`,
    )
    void writeCachedChapter(key, loaded)
    return loaded
  }

  async function prefetchChapter(chapterId: string): Promise<void> {
    if (chapterCache.has(chapterId)) return
    try {
      const persistent = await readCachedChapter(`chapter:${chapterId}`) as ChapterContent | null
      if (persistent) {
        chapterCache.set(chapterId, persistent)
        return
      }
      const loaded = await api.get<ChapterContent>(`/chapters/${chapterId}`)
      chapterCache.set(chapterId, loaded)
      void writeCachedChapter(`chapter:${chapterId}`, loaded)
    } catch {
      // Prefetch is best effort; the normal navigation request reports errors.
    }
  }

  function invalidateChapter(chapterId: string): void {
    chapterCache.delete(chapterId)
    void deleteCachedChapter(chapterId)
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

  return { books, loading, error, fetchBooks, fetchBook, fetchChapters, fetchChapter, fetchChapterChunk, prefetchChapter, invalidateChapter, fetchFavorites, toggleFavorite }
})
