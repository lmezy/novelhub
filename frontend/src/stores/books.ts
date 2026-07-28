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
  created_at: string
  updated_at: string
  tag_names: string[]
  author_name: string | null
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
    return api.get<ChapterContent>(`/chapters/${chapterId}`)
  }

  return { books, loading, error, fetchBooks, fetchBook, fetchChapters, fetchChapter }
})