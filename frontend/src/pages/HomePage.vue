<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { useBooksStore, type Book, type ShelfGroup } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"
import BookCover from "../components/BookCover.vue"

const store = useBooksStore()
const auth = useAuthStore()
const i18n = useI18nStore()
const router = useRouter()
interface ShelfProgress {
  book_id: string
  chapter_id: string | null
  position: number
}

const recentReads = ref<{ book: Book; progress: ShelfProgress }[]>([])
let recentRequest = 0
const favoriteBooks = ref<Book[]>([])
const favoriteLoading = ref(true)
const favoriteError = ref("")
let favoritesRequest = 0
const selectedIds = ref<string[]>([])
const managingShelf = ref(false)
const batchDeleting = ref(false)
const batchRemoving = ref(false)
const groups = ref<ShelfGroup[]>([])
const activeGroupId = ref("")
const groupName = ref("")
const groupCreating = ref(false)
const groupSaving = ref(false)
const groupDialog = ref<HTMLDialogElement | null>(null)
const moveDialog = ref<HTMLDialogElement | null>(null)
const moveGroupIds = ref<string[]>([])
const groupForm = ref<Record<string, { name: string; show: boolean }>>({})
const inviteCopied = ref(false)
const inviteCopiedId = ref<string | null>(null)
const invites = ref<any[]>([])
const inviteCreating = ref(false)
const inviteError = ref("")
const invitesLoaded = ref(false)

// The shelf shows novels and comics on the same page; this keeps them apart
// without hiding either.  ``all`` keeps the previous mixed behaviour.
type ShelfKind = "" | "novel" | "comic"
const shelfKind = ref<ShelfKind>((localStorage.getItem("novelhub_shelf_kind") as ShelfKind) || "")
const shelfBooks = computed(() =>
  shelfKind.value
    ? favoriteBooks.value.filter((book) => (book.kind || "novel") === shelfKind.value)
    : favoriteBooks.value
)
const shelfKindCounts = computed(() => ({
  all: favoriteBooks.value.length,
  novel: favoriteBooks.value.filter((book) => (book.kind || "novel") === "novel").length,
  comic: favoriteBooks.value.filter((book) => book.kind === "comic").length,
}))

function setShelfKind(kind: ShelfKind) {
  shelfKind.value = kind
  selectedIds.value = []
  try {
    if (kind) localStorage.setItem("novelhub_shelf_kind", kind)
    else localStorage.removeItem("novelhub_shelf_kind")
  } catch { /* storage is optional */ }
}

function searchByField(field: "author" | "tags" | "category", value: string) {
  router.push({ path: "/search", query: { field, q: value } })
}

const showCovers = computed(() => auth.user?.settings?.show_covers !== false)

const allSelected = computed(() =>
  shelfBooks.value.length > 0 &&
  shelfBooks.value.every((book) => selectedIds.value.includes(book.id))
)

const shelfKinds: { value: ShelfKind; label: string; count: "all" | "novel" | "comic" }[] = [
  { value: "", label: "kind_all", count: "all" },
  { value: "novel", label: "kind_novels", count: "novel" },
  { value: "comic", label: "kind_comics", count: "comic" },
]

function readingLink(book: Book, chapterId: string | null) {
  return chapterId
    ? { name: "reader", params: { bookId: book.id, chapterId } }
    : { name: "book-detail", params: { id: book.id } }
}

function toggleShelfManagement() {
  managingShelf.value = !managingShelf.value
  if (!managingShelf.value) clearSelection()
}

const groupNameMap = computed(() =>
  Object.fromEntries(groups.value.map((g) => [g.id, g.name]))
)

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement("textarea")
      ta.value = text
      ta.style.position = "fixed"
      ta.style.opacity = "0"
      document.body.appendChild(ta)
      ta.select()
      document.execCommand("copy")
      document.body.removeChild(ta)
    }
    return true
  } catch {
    return false
  }
}

async function copyInviteLink(invite?: any) {
  const code = invite?.code || auth.user?.invite_code
  if (!code) return
  const link = window.location.origin + "/login?invite=" + encodeURIComponent(code)
  const ok = await copyText(link)
  if (!ok) {
    inviteError.value = i18n.t('home_invite_copy_failed')
    return
  }
  inviteCopied.value = true
  inviteCopiedId.value = invite?.id || "permanent"
  setTimeout(() => {
    inviteCopied.value = false
    inviteCopiedId.value = null
  }, 2000)
}

async function loadInvites() {
  try {
    invites.value = await api.get<any[]>("/invites")
  } catch {
    invites.value = []
  }
}

function loadInvitesOnOpen(event: Event) {
  if (!(event.target instanceof HTMLDetailsElement) || !event.target.open || invitesLoaded.value) return
  invitesLoaded.value = true
  void loadInvites()
}

async function createInvite() {
  inviteCreating.value = true
  inviteError.value = ""
  try {
    const created = await api.post<any>("/invites")
    await loadInvites()
    await copyInviteLink(created)
  } catch (e) {
    inviteError.value = e instanceof Error ? e.message : i18n.t('home_invite_create_failed')
  } finally {
    inviteCreating.value = false
  }
}

async function deleteInvite(id: string) {
  try {
    await api.delete("/invites/" + id)
    await loadInvites()
  } catch (e) {
    inviteError.value = e instanceof Error ? e.message : i18n.t('home_invite_delete_failed')
  }
}

async function loadGroups() {
  try {
    groups.value = await api.get<ShelfGroup[]>("/bookshelf/groups")
  } catch {
    groups.value = []
  }
}

async function loadFavorites() {
  const request = ++favoritesRequest
  favoriteLoading.value = true
  favoriteError.value = ""
  try {
    const books = await store.fetchFavorites(activeGroupId.value || undefined)
    if (request === favoritesRequest) favoriteBooks.value = books
  } catch (e) {
    if (request === favoritesRequest) {
      favoriteError.value = e instanceof Error ? e.message : i18n.t('home_load_favorites_failed')
    }
  } finally {
    if (request === favoritesRequest) favoriteLoading.value = false
  }
}

async function toggleFavorite(book: Book) {
  const isFavorite = await store.toggleFavorite(book)
  if (!isFavorite) {
    favoriteBooks.value = favoriteBooks.value.filter((b) => b.id !== book.id)
    selectedIds.value = selectedIds.value.filter((id) => id !== book.id)
    await loadGroups()
  }
}

async function deleteBook(id: string, title: string) {
  if (!confirm(i18n.t('home_delete_confirm', { title }))) return
  try {
    await api.delete('/books/' + id)
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
    await loadFavorites()
    await loadGroups()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_delete_failed'))
  }
}

function toggleSelect(id: string) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((x) => x !== id)
    : [...selectedIds.value, id]
}

function selectAll() {
  selectedIds.value = shelfBooks.value.map((book) => book.id)
}

function invertSelection() {
  const selected = new Set(selectedIds.value)
  selectedIds.value = shelfBooks.value
    .filter((book) => !selected.has(book.id))
    .map((book) => book.id)
}

function clearSelection() {
  selectedIds.value = []
}

async function batchDelete() {
  if (!selectedIds.value.length) return
  if (!confirm(i18n.t('home_batch_delete_confirm', { n: selectedIds.value.length }))) return
  batchDeleting.value = true
  try {
    await api.post('/books/batch-delete', { ids: selectedIds.value })
    selectedIds.value = []
    await loadFavorites()
    await loadGroups()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_batch_delete_failed'))
  } finally {
    batchDeleting.value = false
  }
}



async function batchRemoveShelf() {
  if (!selectedIds.value.length) return
  batchRemoving.value = true
  try {
    const res = await api.post('/bookshelf/batch-unfavorite', { ids: selectedIds.value }) as { removed: number }
    alert(i18n.t('home_batch_remove_done', { n: res.removed }))
    selectedIds.value = []
    await loadFavorites()
    await loadGroups()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_batch_remove_failed'))
  } finally {
    batchRemoving.value = false
  }
}

async function selectGroup(id: string) {
  activeGroupId.value = id
  selectedIds.value = []
  await loadFavorites()
}

async function createGroup() {
  const name = groupName.value.trim()
  if (!name) return
  groupCreating.value = true
  try {
    await api.post('/bookshelf/groups', { name })
    groupName.value = ""
    await loadGroups()
    for (const group of groups.value) {
      groupForm.value[group.id] ??= { name: group.name, show: group.show }
    }
    if (!activeGroupId.value) await loadFavorites()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_group_create_failed'))
  } finally {
    groupCreating.value = false
  }
}

function startManageGroups() {
  groupForm.value = Object.fromEntries(
    groups.value.map((g) => [g.id, { name: g.name, show: g.show }])
  )
  groupDialog.value?.showModal()
}

async function saveGroup(group: ShelfGroup) {
  const form = groupForm.value[group.id]
  if (!form || !form.name.trim()) return
  groupSaving.value = true
  try {
    await api.patch('/bookshelf/groups/' + group.id, {
      name: form.name.trim(),
      show: form.show,
    })
    await loadGroups()
    await loadFavorites()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_group_save_failed'))
  } finally {
    groupSaving.value = false
  }
}

async function deleteGroup(group: ShelfGroup) {
  if (!confirm(i18n.t('home_group_delete_confirm', { name: group.name }))) return
  try {
    await api.delete('/bookshelf/groups/' + group.id)
    if (activeGroupId.value === group.id) {
      activeGroupId.value = ""
    }
    await loadGroups()
    await loadFavorites()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_group_delete_failed'))
  }
}

function openMoveGroups() {
  moveGroupIds.value = []
  moveDialog.value?.showModal()
}

async function saveMoveGroups() {
  if (!selectedIds.value.length) return
  groupSaving.value = true
  try {
    await api.post('/bookshelf/favorites/batch-groups', {
      book_ids: selectedIds.value,
      group_ids: moveGroupIds.value,
    })
    moveDialog.value?.close()
    selectedIds.value = []
    await loadFavorites()
    await loadGroups()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_group_move_failed'))
  } finally {
    groupSaving.value = false
  }
}

async function toggleSelfVisibility(key: "r18_enabled" | "non_r18_enabled") {
  if (!auth.user) return
  try {
    await auth.updateVisibility({ [key]: !auth.user[key] })
    selectedIds.value = []
    await Promise.all([loadFavorites(), loadRecentReads()])
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_update_failed'))
  }
}

async function loadRecentReads() {
  if (!auth.user) return
  const request = ++recentRequest
  recentReads.value = []
  try {
    const progress = await api.get<ShelfProgress[]>('/progress?user_id=' + encodeURIComponent(auth.user.id))
    const pageSize = 4
    const visible: typeof recentReads.value = []
    for (let offset = 0; offset < progress.length && visible.length < pageSize;) {
      if (request !== recentRequest) return
      const batch = progress.slice(offset, offset + pageSize - visible.length)
      offset += batch.length
      const results = await Promise.all(
        batch.map(async (entry) => {
          try {
            return { book: await store.fetchBook(entry.book_id), progress: entry }
          } catch {
            return null
          }
        }),
      )
      visible.push(...results.filter((entry) => entry !== null))
    }
    if (request === recentRequest) recentReads.value = visible
  } catch { /* non-critical */ }
}

onMounted(() => {
  void loadGroups()
  void loadFavorites()
  void loadRecentReads()
})
</script>

<template>
  <div class="min-h-screen bg-paper text-ink dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="mx-auto max-w-6xl space-y-8 px-4 py-6 sm:space-y-10 sm:px-6 sm:py-10">
      <header class="flex flex-wrap items-center justify-between gap-3">
        <h1 class="font-serif text-4xl font-semibold tracking-tight sm:text-5xl">{{ i18n.t('home_library') }}</h1>
        <router-link to="/books" class="shelf-button border-accent/35 text-accent dark:border-emerald-300/35 dark:text-emerald-200">
          {{ i18n.t('home_goto_books') }} <span aria-hidden="true">↗</span>
        </router-link>
      </header>

      <section v-if="recentReads.length" aria-labelledby="shelf-continue-title">
        <h2 id="shelf-continue-title" class="mb-3 text-lg font-semibold">{{ i18n.t('home_continue') }}</h2>
        <div class="grid gap-3" :class="{ 'md:grid-cols-[1.1fr_1fr]': recentReads.length > 1 }">
          <router-link
            :to="readingLink(recentReads[0].book, recentReads[0].progress.chapter_id)"
            class="flex min-w-0 items-center gap-4 rounded-2xl bg-[#23493f] p-4 text-white no-underline transition-colors hover:bg-[#2b584c] sm:gap-5 sm:p-5"
          >
            <BookCover
              v-if="showCovers"
              :src="recentReads[0].book.cover"
              priority
              class="h-28 w-20 shrink-0 rounded-lg shadow-lg sm:h-36 sm:w-24"
            />
            <div class="min-w-0 flex-1">
              <h3 class="line-clamp-2 break-words font-serif text-xl font-semibold sm:text-2xl">{{ recentReads[0].book.title }}</h3>
              <p v-if="recentReads[0].book.author_name" class="mt-1 truncate text-sm text-[#cbdfd5]">{{ recentReads[0].book.author_name }}</p>
              <div v-if="recentReads[0].progress.chapter_id" class="mt-3 flex items-center gap-2">
                <progress
                  :value="recentReads[0].progress.position"
                  max="100"
                  :aria-label="i18n.t('reader_reading_progress')"
                  class="shelf-progress min-w-0 max-w-48 flex-1"
                />
                <span class="text-xs tabular-nums text-[#e3f0e8]">{{ recentReads[0].progress.position }}%</span>
              </div>
            </div>
            <span aria-hidden="true" class="shrink-0 text-lg text-[#e3f0e8]">→</span>
          </router-link>
          <div v-if="recentReads.length > 1" class="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-surface dark:divide-gray-800 dark:border-gray-800 dark:bg-gray-900">
            <router-link
              v-for="entry in recentReads.slice(1)"
              :key="entry.book.id"
              :to="readingLink(entry.book, entry.progress.chapter_id)"
              class="flex min-w-0 items-center gap-3 px-4 py-2.5 no-underline hover:bg-accent/5 dark:hover:bg-gray-800"
            >
              <BookCover v-if="showCovers" :src="entry.book.cover" class="h-9 w-7 shrink-0 rounded" />
              <span class="min-w-0 flex-1">
                <span class="block truncate text-sm font-semibold">{{ entry.book.title }}</span>
                <small v-if="entry.book.author_name" class="block truncate text-xs text-muted dark:text-gray-400">{{ entry.book.author_name }}</small>
              </span>
              <span v-if="entry.progress.chapter_id" class="text-xs tabular-nums text-muted dark:text-gray-400">{{ entry.progress.position }}%</span>
              <span aria-hidden="true" class="shrink-0 text-accent dark:text-emerald-200">→</span>
            </router-link>
          </div>
        </div>
      </section>

      <section aria-labelledby="shelf-favorites-title">
        <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-baseline gap-2">
            <h2 id="shelf-favorites-title" class="text-lg font-semibold">{{ i18n.t('home_favorites_title') }}</h2>
            <span v-if="!favoriteLoading" class="text-xs tabular-nums text-muted dark:text-gray-400">{{ i18n.t('home_books_count', { n: shelfBooks.length }) }}</span>
          </div>
          <button
            v-if="shelfBooks.length || managingShelf || (auth.user?.can_manage_visibility && !auth.isAdmin)"
            type="button"
            class="shelf-button"
            :aria-pressed="managingShelf"
            @click="toggleShelfManagement"
          >
            {{ i18n.t(managingShelf ? 'home_manage_done' : 'home_manage_shelf') }}
          </button>
        </div>

        <div class="mb-4 divide-y divide-border rounded-2xl border border-border bg-surface dark:divide-gray-800 dark:border-gray-800 dark:bg-gray-900">
          <div class="flex flex-wrap items-center gap-2 px-3 py-2 sm:px-4">
            <button
              v-for="kind in shelfKinds"
              :key="kind.value"
              type="button"
              :aria-pressed="shelfKind === kind.value"
              class="shelf-filter"
              @click="setShelfKind(kind.value)"
            >
              {{ i18n.t(kind.label) }}
              <span class="ml-1 text-[11px] opacity-75">{{ shelfKindCounts[kind.count] }}</span>
            </button>
          </div>
          <div class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 sm:flex-nowrap sm:px-4">
            <div class="flex min-w-0 flex-1 flex-wrap items-center gap-2">
              <span class="mr-1 text-xs font-medium text-muted dark:text-gray-400">{{ i18n.t('home_group_books') }}</span>
              <button type="button" class="shelf-filter" :aria-pressed="!activeGroupId" @click="selectGroup('')">{{ i18n.t('home_group_all') }}</button>
              <button
                v-for="group in groups.filter(group => group.show || group.id === activeGroupId)"
                :key="group.id"
                type="button"
                class="shelf-filter max-w-full truncate"
                :aria-pressed="activeGroupId === group.id"
                @click="selectGroup(group.id)"
              >{{ group.name }} <span class="ml-1 text-[11px] opacity-75">{{ group.count }}</span></button>
            </div>
            <button type="button" class="shelf-button shrink-0" @click="startManageGroups">{{ i18n.t('home_group_manage') }}</button>
          </div>
        </div>

        <div v-if="managingShelf" class="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-accent/25 bg-accent/5 p-3 text-xs dark:border-gray-700 dark:bg-gray-900">
          <template v-if="shelfBooks.length && !favoriteLoading">
            <label class="mr-2 inline-flex min-h-9 cursor-pointer items-center gap-2">
              <input type="checkbox" :checked="allSelected" class="accent-accent" @change="allSelected ? clearSelection() : selectAll()" />
              {{ i18n.t('books_select_all') }}
            </label>
            <button type="button" class="shelf-button" @click="invertSelection">{{ i18n.t('books_select_invert') }}</button>
            <button type="button" class="shelf-button" :disabled="!selectedIds.length" @click="clearSelection">{{ i18n.t('books_select_none') }}</button>
            <span class="px-1 text-muted dark:text-gray-400">{{ i18n.t('books_selected_count', { n: selectedIds.length }) }}</span>
            <template v-if="selectedIds.length">
              <button type="button" class="shelf-button" :disabled="batchRemoving" @click="batchRemoveShelf">{{ i18n.t('home_batch_remove_shelf') }}</button>
              <button type="button" class="shelf-button" @click="openMoveGroups">{{ i18n.t('home_group_move') }}</button>
              <button v-if="auth.isAdmin" type="button" class="shelf-button text-red-600 dark:text-red-300" :disabled="batchDeleting" @click="batchDelete">{{ i18n.t('books_batch_delete') }}</button>
            </template>
          </template>
          <template v-if="auth.user?.can_manage_visibility && !auth.isAdmin">
            <button type="button" class="shelf-button" :aria-pressed="auth.user.r18_enabled" @click="toggleSelfVisibility('r18_enabled')">{{ i18n.t(auth.user.r18_enabled ? 'home_r18_on' : 'home_r18_off') }}</button>
            <button type="button" class="shelf-button" :aria-pressed="auth.user.non_r18_enabled" @click="toggleSelfVisibility('non_r18_enabled')">{{ i18n.t(auth.user.non_r18_enabled ? 'home_all_ages_on' : 'home_all_ages_off') }}</button>
          </template>
        </div>

        <p v-if="favoriteLoading" role="status" class="py-10 text-center text-sm text-muted dark:text-gray-400">{{ i18n.t('home_loading') }}</p>
        <p v-else-if="favoriteError" role="alert" class="py-10 text-center text-sm text-red-600 dark:text-red-300">{{ favoriteError }}</p>
        <div v-else-if="!shelfBooks.length" class="rounded-2xl border border-dashed border-border bg-surface px-4 py-10 text-center dark:border-gray-800 dark:bg-gray-900">
          <p class="font-semibold">{{ i18n.t(favoriteBooks.length ? 'home_shelf_kind_empty' : 'home_shelf_empty') }}</p>
          <p class="mx-auto mt-1 max-w-xs text-sm text-muted dark:text-gray-400">{{ i18n.t(favoriteBooks.length ? 'home_shelf_kind_empty_hint' : 'home_shelf_empty_hint') }}</p>
          <router-link to="/books" class="shelf-button mt-4 text-accent dark:text-emerald-200">{{ i18n.t('home_goto_books') }}</router-link>
        </div>
        <div v-else class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="book in shelfBooks"
            :key="book.id"
            class="relative flex min-w-0 items-start gap-3 rounded-2xl border bg-surface p-3 transition-colors hover:border-accent/45 dark:bg-gray-900"
            :class="selectedIds.includes(book.id) ? 'border-accent ring-1 ring-accent/35' : 'border-border dark:border-gray-800'"
          >
            <label v-if="managingShelf" class="relative z-10 grid h-9 w-7 shrink-0 cursor-pointer place-items-center">
              <input
                type="checkbox"
                :checked="selectedIds.includes(book.id)"
                :aria-label="i18n.t('books_select_title', { title: book.title })"
                class="h-4 w-4 accent-accent"
                @change="toggleSelect(book.id)"
              />
            </label>
            <BookCover v-if="showCovers" :src="book.cover" class="h-24 w-[4.25rem] shrink-0 rounded-lg sm:h-28 sm:w-20" />
            <div class="min-w-0 flex-1">
              <h3 class="pr-9 text-sm font-semibold leading-snug sm:text-base">
                <router-link
                  :to="{ name: 'book-detail', params: { id: book.id } }"
                  class="line-clamp-2 break-words no-underline after:absolute after:inset-0 after:rounded-2xl focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-accent"
                >{{ book.title }}</router-link>
              </h3>
              <button
                v-if="book.author_name"
                type="button"
                class="relative z-10 mt-1 max-w-full truncate text-left text-xs text-muted hover:text-accent dark:text-gray-400 dark:hover:text-emerald-200"
                :title="i18n.t('book_author_search')"
                @click="searchByField('author', book.author_name)"
              >{{ book.author_name }}</button>
              <p v-if="book.description" class="mt-1 line-clamp-2 text-xs leading-5 text-muted dark:text-gray-400">{{ book.description }}</p>
              <div v-if="book.shelf_group_ids?.length || book.tag_names?.length || book.custom_tags?.length || book.kind === 'comic'" class="mt-2 flex flex-wrap gap-1.5">
                <span v-if="book.kind === 'comic'" class="rounded-md bg-[#f1e5d8] px-1.5 py-0.5 text-[11px] text-[#704c35] dark:bg-[#473628] dark:text-[#edd7ba]">{{ i18n.t('kind_comics') }}</span>
                <span
                  v-for="id in (book.shelf_group_ids || []).filter(id => groupNameMap[id])"
                  :key="id"
                  class="max-w-full truncate rounded-md bg-accent/10 px-1.5 py-0.5 text-[11px] text-accent dark:bg-emerald-300/10 dark:text-emerald-200"
                >{{ groupNameMap[id] }}</span>
                <button
                  v-for="tag in book.tag_names"
                  :key="tag"
                  type="button"
                  :title="i18n.t('book_tag_search')"
                  class="relative z-10 max-w-full truncate rounded-md bg-paper px-1.5 py-0.5 text-[11px] text-muted hover:text-accent dark:bg-gray-800 dark:text-gray-300 dark:hover:text-emerald-200"
                  @click="searchByField('tags', tag)"
                >{{ tag }}</button>
                <button
                  v-for="tag in book.custom_tags || []"
                  :key="tag.id"
                  type="button"
                  :title="i18n.t('book_tag_search')"
                  class="relative z-10 max-w-full truncate rounded-md bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-800 dark:bg-amber-900/45 dark:text-amber-200"
                  @click="searchByField('tags', tag.name)"
                >{{ tag.name }}<template v-if="tag.count > 1"> ×{{ tag.count }}</template></button>
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted dark:text-gray-400">
                <span v-if="book.status">{{ book.status === 'completed' ? i18n.t('home_completed') : book.status }}</span>
                <span v-if="book.updated_at">{{ new Date(book.updated_at).toLocaleDateString() }}</span>
                <button v-if="auth.isAdmin && managingShelf" type="button" class="relative z-10 text-red-600 hover:underline dark:text-red-300" @click="deleteBook(book.id, book.title)">{{ i18n.t('home_delete_title') }}</button>
              </div>
            </div>
            <button
              type="button"
              class="absolute right-2 top-2 z-10 grid h-9 w-9 place-items-center rounded-full text-lg text-amber-700 hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-900/40"
              :title="i18n.t('home_favorite_remove')"
              :aria-label="i18n.t('home_favorite_remove')"
              @click="toggleFavorite(book)"
            ><span aria-hidden="true">★</span></button>
          </article>
        </div>
      </section>

      <details
        v-if="auth.user"
        class="group rounded-2xl border border-border bg-surface dark:border-gray-800 dark:bg-gray-900"
        @toggle="loadInvitesOnOpen"
      >
        <summary class="flex min-h-12 cursor-pointer list-none items-center justify-between gap-2 px-4 text-sm font-semibold sm:px-5">
          {{ i18n.t('home_invite_title') }} <span class="text-muted dark:text-gray-400 group-open:rotate-180" aria-hidden="true">⌄</span>
        </summary>
        <div class="border-t border-border p-4 dark:border-gray-800 sm:px-5">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <p class="text-sm text-muted dark:text-gray-400">{{ i18n.t('home_invite_hint') }}</p>
            <button type="button" class="shelf-button text-accent dark:text-emerald-200" :disabled="inviteCreating" @click="createInvite">{{ i18n.t(inviteCreating ? 'home_invite_creating' : 'home_invite_create') }}</button>
          </div>
          <p v-if="inviteError" role="alert" class="mt-2 text-xs text-red-600 dark:text-red-300">{{ inviteError }}</p>
          <ul v-if="invites.length" class="mt-3 divide-y divide-border dark:divide-gray-800">
            <li v-for="invite in invites" :key="invite.id" class="flex flex-wrap items-center justify-between gap-2 py-2">
              <div class="min-w-0">
                <span class="font-mono text-sm">{{ invite.code }}</span>
                <span class="ml-2 text-xs text-muted dark:text-gray-400">{{ invite.used_at ? i18n.t('home_invite_used') : i18n.t('home_invite_valid_until', { time: new Date(invite.expires_at).toLocaleString() }) }}</span>
              </div>
              <div class="flex items-center gap-2">
                <button type="button" class="shelf-button" :disabled="!!invite.used_at" @click="copyInviteLink(invite)">{{ i18n.t(inviteCopied && inviteCopiedId === invite.id ? 'home_invite_copied' : 'home_invite_copy_link') }}</button>
                <button type="button" class="shelf-button text-red-600 dark:text-red-300" @click="deleteInvite(invite.id)">{{ i18n.t('home_invite_delete') }}</button>
              </div>
            </li>
          </ul>
          <p v-else class="mt-3 text-xs text-muted dark:text-gray-400">{{ i18n.t('home_invite_no_items') }}</p>
        </div>
      </details>
    </main>

    <dialog ref="moveDialog" aria-labelledby="shelf-move-title" class="shelf-dialog">
        <h3 id="shelf-move-title" class="mb-3 text-base font-semibold">{{ i18n.t('home_group_move') }}</h3>
        <label v-for="group in groups" :key="group.id" class="flex min-h-9 cursor-pointer items-center gap-2 text-sm">
          <input v-model="moveGroupIds" type="checkbox" :value="group.id" class="accent-accent" />
          {{ group.name }}
        </label>
        <p class="mt-2 text-xs text-muted dark:text-gray-400">{{ i18n.t('home_group_move_hint') }}</p>
        <div class="mt-4 flex gap-2">
          <button type="button" class="shelf-button bg-accent text-white" :disabled="groupSaving" @click="saveMoveGroups">{{ i18n.t('home_group_save') }}</button>
          <button type="button" class="shelf-button" @click="moveDialog?.close()">{{ i18n.t('home_cancel') }}</button>
        </div>
    </dialog>

    <dialog ref="groupDialog" aria-labelledby="shelf-groups-title" class="shelf-dialog">
        <h3 id="shelf-groups-title" class="mb-3 text-base font-semibold">{{ i18n.t('home_group_manage') }}</h3>
        <form class="mb-4 flex flex-wrap items-center gap-2" @submit.prevent="createGroup">
          <input
            v-model="groupName"
            :aria-label="i18n.t('home_group_name_placeholder')"
            :placeholder="i18n.t('home_group_name_placeholder')"
            class="min-h-10 min-w-28 flex-1 rounded-lg border border-border bg-paper px-3 text-sm dark:border-gray-700 dark:bg-gray-800"
            required
          />
          <button type="submit" class="shelf-button bg-accent text-white" :disabled="groupCreating || !groupName.trim()">{{ i18n.t('home_group_create') }}</button>
        </form>
        <p v-if="!groups.length" class="text-sm text-muted dark:text-gray-400">{{ i18n.t('home_group_empty') }}</p>
        <div v-for="group in groups" :key="group.id" class="border-t border-border py-2 dark:border-gray-800">
          <form v-if="groupForm[group.id]" class="flex flex-wrap items-center gap-2" @submit.prevent="saveGroup(group)">
            <input
              v-model="groupForm[group.id].name"
              :aria-label="i18n.t('home_group_name_placeholder')"
              class="min-h-9 min-w-28 flex-1 rounded-lg border border-border bg-paper px-3 text-sm dark:border-gray-700 dark:bg-gray-800"
              required
            />
            <label class="inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted dark:text-gray-400">
              <input v-model="groupForm[group.id].show" type="checkbox" class="accent-accent" />
              {{ i18n.t('home_group_show') }}
            </label>
            <button type="submit" class="shelf-button" :disabled="groupSaving">{{ i18n.t('home_group_save') }}</button>
            <button type="button" class="shelf-button text-red-600 dark:text-red-300" :disabled="groupSaving" @click="deleteGroup(group)">{{ i18n.t('home_group_delete') }}</button>
          </form>
        </div>
        <button type="button" class="shelf-button mt-4" @click="groupDialog?.close()">{{ i18n.t('home_close') }}</button>
    </dialog>
  </div>
</template>

<style scoped>
.shelf-button {
  @apply inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg border border-border px-3 text-xs font-semibold no-underline transition-colors hover:border-accent/40 disabled:opacity-45 dark:border-gray-700;
}

.shelf-filter {
  @apply min-h-9 rounded-lg px-2.5 text-xs font-medium text-muted hover:bg-accent/10 dark:text-gray-400 dark:hover:bg-gray-800;
}

.shelf-filter[aria-pressed="true"] {
  @apply bg-accent/10 text-accent dark:bg-emerald-300/10 dark:text-emerald-200;
}

.shelf-progress {
  @apply h-1.5 overflow-hidden rounded-full;
  appearance: none;
}

.shelf-progress::-webkit-progress-bar {
  background: #42675d;
  border-radius: 0.75rem;
}

.shelf-progress::-webkit-progress-value {
  background: #c1dfce;
  border-radius: 0.75rem;
}

.shelf-progress::-moz-progress-bar {
  background: #c1dfce;
  border-radius: 0.75rem;
}

.shelf-dialog {
  @apply fixed inset-0 m-auto h-fit max-h-[85dvh] w-[calc(100%-2rem)] max-w-md overflow-auto rounded-2xl border border-border bg-surface p-4 text-ink shadow-xl dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 sm:p-5;
}

.shelf-dialog::backdrop {
  @apply bg-black/45;
}

summary::-webkit-details-marker {
  display: none;
}
</style>
