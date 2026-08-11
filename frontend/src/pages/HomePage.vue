<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { useBooksStore, type Book, type ShelfGroup } from "../stores/books"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import { api } from "../api/client"
import NavBar from "../components/NavBar.vue"

const store = useBooksStore()
const auth = useAuthStore()
const i18n = useI18nStore()
const router = useRouter()
const recentReads = ref<Book[]>([])
const favoriteBooks = ref<Book[]>([])
const favoriteLoading = ref(false)
const favoriteError = ref("")
const selectedIds = ref<string[]>([])
const batchDeleting = ref(false)
const batchRemoving = ref(false)
const groups = ref<ShelfGroup[]>([])
const activeGroupId = ref("")
const groupName = ref("")
const groupCreating = ref(false)
const groupSaving = ref(false)
const managingGroups = ref(false)
const moveOpen = ref(false)
const moveGroupIds = ref<string[]>([])
const groupForm = ref<Record<string, { name: string; show: boolean }>>({})
const inviteCopied = ref(false)
const inviteCopiedId = ref<string | null>(null)
const invites = ref<any[]>([])
const inviteCreating = ref(false)
const inviteError = ref("")

function searchByField(field: "author" | "tags", value: string) {
  router.push({ path: "/search", query: { field, q: value } })
}

const showCovers = computed(() => auth.user?.settings?.show_covers !== false)

const allSelected = computed(() =>
  favoriteBooks.value.length > 0 &&
  selectedIds.value.length === favoriteBooks.value.length
)

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
  favoriteLoading.value = true
  favoriteError.value = ""
  try {
    favoriteBooks.value = await store.fetchFavorites(activeGroupId.value || undefined)
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
  selectedIds.value = favoriteBooks.value.map((book) => book.id)
}

function invertSelection() {
  const selected = new Set(selectedIds.value)
  selectedIds.value = favoriteBooks.value
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
  managingGroups.value = true
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
  moveOpen.value = true
}

async function saveMoveGroups() {
  if (!selectedIds.value.length) return
  groupSaving.value = true
  try {
    await api.post('/bookshelf/favorites/batch-groups', {
      book_ids: selectedIds.value,
      group_ids: moveGroupIds.value,
    })
    moveOpen.value = false
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
    await loadFavorites()
  } catch (e) {
    alert(e instanceof Error ? e.message : i18n.t('home_update_failed'))
  }
}

onMounted(async () => {
  await loadGroups()
  await loadFavorites()
  if (auth.user) {
    await loadInvites()
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
      <section v-if="auth.user" class="mb-8 p-4 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
        <div class="flex items-center justify-between flex-wrap gap-3">
          <div>
            <p class="text-sm font-medium">{{ i18n.t('home_invite_title') }}</p>
            <p class="text-xs text-muted dark:text-gray-400 mt-1">{{ i18n.t('home_invite_hint') }}</p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button
              @click="createInvite"
              :disabled="inviteCreating"
              class="text-xs px-3 py-1.5 rounded bg-accent text-white hover:opacity-90 disabled:opacity-50"
            >{{ inviteCreating ? i18n.t('home_invite_creating') : i18n.t('home_invite_create') }}</button>
          </div>
        </div>
        <p v-if="inviteError" class="text-xs text-red-600 mt-2">{{ inviteError }}</p>
        <div v-if="invites.length" class="mt-4 divide-y divide-border border-t border-border dark:border-gray-700">
          <div v-for="inv in invites" :key="inv.id" class="py-2 flex items-center justify-between gap-3">
            <div class="min-w-0">
              <span class="text-sm font-mono">{{ inv.code }}</span>
              <span class="text-xs text-muted dark:text-gray-400 ml-2">
                {{ inv.used_at ? i18n.t('home_invite_used') : i18n.t('home_invite_valid_until', { time: new Date(inv.expires_at).toLocaleString() }) }}
              </span>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <button
                @click="copyInviteLink(inv)"
                :disabled="!!inv.used_at"
                class="text-xs px-2 py-1 rounded border border-accent text-accent hover:bg-accent/10 disabled:opacity-40"
              >{{ inviteCopied && inviteCopiedId === inv.id ? i18n.t('home_invite_copied') : i18n.t('home_invite_copy_link') }}</button>
              <button @click="deleteInvite(inv.id)" class="text-xs px-2 py-1 rounded border border-red-300 text-red-500 hover:bg-red-50">{{ i18n.t('home_invite_delete') }}</button>
            </div>
          </div>
        </div>
        <p v-else class="text-xs text-muted dark:text-gray-400 mt-3">{{ i18n.t('home_invite_no_items') }}</p>
      </section>

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
              v-if="selectedIds.length"
              @click="batchRemoveShelf"
              :disabled="batchRemoving"
              class="text-xs px-3 py-1.5 rounded bg-accent text-white hover:opacity-90 disabled:opacity-50"
            >{{ i18n.t('home_batch_remove_shelf') }} ({{ selectedIds.length }})</button>
            <button
              v-if="selectedIds.length"
              @click="openMoveGroups"
              class="text-xs px-3 py-1.5 rounded border border-border dark:border-gray-700 hover:bg-accent/5"
            >{{ i18n.t('home_group_move') }}</button>
            <button
              v-if="auth.isAdmin && selectedIds.length"
              @click="batchDelete"
              :disabled="batchDeleting"
              class="text-xs px-3 py-1.5 rounded bg-red-500 text-white hover:bg-red-600 disabled:opacity-50"
            >{{ i18n.t('books_batch_delete') }} ({{ selectedIds.length }})</button>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2 mb-4">
          <button
            @click="selectGroup('')"
            class="text-xs px-3 py-1.5 rounded border"
            :class="activeGroupId === '' ? 'bg-accent text-white border-accent' : 'border-border dark:border-gray-700 text-muted dark:text-gray-400 hover:bg-accent/5'"
          >{{ i18n.t('home_group_all') }}</button>
          <button
            v-for="g in groups.filter(g => g.show)"
            :key="g.id"
            @click="selectGroup(g.id)"
            class="text-xs px-3 py-1.5 rounded border"
            :class="activeGroupId === g.id ? 'bg-accent text-white border-accent' : 'border-border dark:border-gray-700 text-muted dark:text-gray-400 hover:bg-accent/5'"
          >{{ g.name }} ({{ g.count }})</button>
          <div class="inline-flex items-center gap-1">
            <input
              v-model="groupName"
              :placeholder="i18n.t('home_group_name_placeholder')"
              @keyup.enter="createGroup"
              class="w-32 px-2 py-1.5 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800"
            />
            <button
              @click="createGroup"
              :disabled="groupCreating"
              class="text-xs px-2 py-1.5 rounded bg-accent text-white disabled:opacity-50"
            >{{ i18n.t('home_group_create') }}</button>
          </div>
          <button
            @click="startManageGroups"
            class="text-xs px-2 py-1.5 rounded border border-border dark:border-gray-700 hover:bg-accent/5"
          >{{ i18n.t('home_group_manage') }}</button>
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

        <template v-else>
          <div class="mb-3 flex flex-wrap items-center gap-2 text-xs">
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

          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <router-link
            v-for="book in favoriteBooks"
            :key="book.id"
            :to="'/books/' + book.id"
            class="relative group block p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 hover:shadow-md hover:border-accent/30 transition-all duration-200 no-underline"
          >
            <input
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
            <img
              v-if="showCovers && book.cover"
              :src="book.cover"
              :alt="book.title"
              class="w-full h-44 object-cover rounded-md mb-3 border border-border dark:border-gray-700"
            />
            <h3 class="font-semibold text-ink mb-1 truncate">{{ book.title }}</h3>
            <button
              v-if="book.author_name"
              @click.prevent.stop="searchByField('author', book.author_name)"
              :title="i18n.t('book_author_search')"
              class="text-xs text-muted dark:text-gray-400 mb-1 hover:text-accent transition-colors"
            >{{ book.author_name }}</button>
            <p v-if="book.shelf_group_ids?.length" class="text-xs text-accent/80 dark:text-accent/70 mb-1">
              {{ i18n.t('home_group_books') }}: {{ book.shelf_group_ids.map(id => groupNameMap[id]).filter(Boolean).join(', ') }}
            </p>
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

        <div v-if="moveOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="moveOpen = false">
          <div class="w-full max-w-sm rounded-lg border border-border dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
            <h3 class="text-sm font-semibold mb-3">{{ i18n.t('home_group_move') }}</h3>
            <label v-for="g in groups" :key="g.id" class="flex items-center gap-2 py-1.5 text-sm cursor-pointer">
              <input type="checkbox" :value="g.id" v-model="moveGroupIds" class="rounded" />
              <span>{{ g.name }}</span>
            </label>
            <p class="text-xs text-muted dark:text-gray-400 mt-2">{{ i18n.t('home_group_move_hint') }}</p>
            <div class="flex gap-2 mt-4">
              <button @click="saveMoveGroups" :disabled="groupSaving" class="px-3 py-1.5 rounded bg-accent text-white text-xs disabled:opacity-50">{{ i18n.t('home_group_save') }}</button>
              <button @click="moveOpen = false" class="px-3 py-1.5 rounded border border-border dark:border-gray-700 text-xs">{{ i18n.t('home_cancel') }}</button>
            </div>
          </div>
        </div>

        <div v-if="managingGroups" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="managingGroups = false">
          <div class="w-full max-w-md rounded-lg border border-border dark:border-gray-700 bg-white dark:bg-gray-900 p-4 max-h-[80vh] overflow-y-auto">
            <h3 class="text-sm font-semibold mb-3">{{ i18n.t('home_group_manage') }}</h3>
            <p v-if="groups.length === 0" class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('home_group_empty') }}</p>
            <div v-for="g in groups" :key="g.id" class="py-2 border-b border-border dark:border-gray-700 last:border-0">
              <div class="flex flex-wrap items-center gap-2">
                <input v-model="groupForm[g.id].name" class="flex-1 min-w-24 px-2 py-1.5 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
                <label class="inline-flex items-center gap-1 text-xs text-muted dark:text-gray-400 cursor-pointer">
                  <input type="checkbox" v-model="groupForm[g.id].show" class="rounded" />
                  {{ i18n.t('home_group_show') }}
                </label>
                <button @click="saveGroup(g)" :disabled="groupSaving" class="text-xs px-2 py-1 rounded border border-accent text-accent hover:bg-accent/10">{{ i18n.t('home_group_save') }}</button>
                <button @click="deleteGroup(g)" class="text-xs px-2 py-1 rounded border border-red-200 text-red-500 hover:bg-red-50">{{ i18n.t('home_group_delete') }}</button>
              </div>
            </div>
            <button @click="managingGroups = false" class="mt-4 px-3 py-1.5 rounded border border-border dark:border-gray-700 text-xs">{{ i18n.t('home_close') }}</button>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>
