<script setup lang="ts">
import type { Book } from "../stores/books"
import { useI18nStore } from "../stores/i18n"
import BookCover from "./BookCover.vue"

defineProps<{
  book: Book
  showCover: boolean
  sourceName?: string
  selectable?: boolean
  selected?: boolean
}>()

const emit = defineEmits<{
  favorite: [book: Book]
  select: [id: string]
  search: [field: "author" | "tags" | "category", value: string]
}>()

const i18n = useI18nStore()
</script>

<template>
  <article
    class="group relative flex min-w-0 flex-col rounded-2xl border bg-surface p-2.5 transition-colors hover:border-accent/50 dark:bg-gray-900 sm:p-3"
    :class="selected ? 'border-accent ring-1 ring-accent/35' : 'border-border dark:border-gray-800'"
  >
    <BookCover v-if="showCover" :src="book.cover" class="mb-3 aspect-[5/7] w-full rounded-xl" />
    <button
      v-if="selectable"
      type="button"
      role="checkbox"
      :aria-checked="Boolean(selected)"
      :aria-label="i18n.t('books_select_title', { title: book.title })"
      class="absolute left-3 top-3 z-10 grid h-9 w-9 place-items-center rounded-full border border-border bg-surface/95 text-accent shadow-sm dark:border-gray-700 dark:bg-gray-900 dark:text-emerald-300"
      @click="emit('select', book.id)"
    >
      <span class="grid h-4 w-4 place-items-center rounded border border-current text-xs" aria-hidden="true">{{ selected ? '✓' : '' }}</span>
    </button>
    <button
      type="button"
      @click="emit('favorite', book)"
      class="absolute right-3 top-3 z-10 grid h-9 w-9 place-items-center rounded-full border border-border bg-surface/95 text-lg leading-none shadow-sm dark:border-gray-700 dark:bg-gray-900"
      :class="book.is_favorite ? 'text-amber-700 dark:text-amber-300' : 'text-muted hover:text-accent dark:text-gray-300'"
      :aria-label="book.is_favorite ? i18n.t('books_favorite_on') : i18n.t('books_favorite_off')"
      :title="book.is_favorite ? i18n.t('books_favorite_on') : i18n.t('books_favorite_off')"
    ><span aria-hidden="true">{{ book.is_favorite ? '★' : '☆' }}</span></button>

    <h3 class="text-sm font-semibold leading-snug text-ink dark:text-gray-100 sm:text-[15px]" :class="{ 'pr-9': !showCover, 'pl-10': !showCover && selectable }">
      <router-link
        :to="'/books/' + book.id"
        class="line-clamp-2 rounded-sm no-underline after:absolute after:inset-0 after:rounded-2xl focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-accent"
      >{{ book.title }}</router-link>
    </h3>
    <button
      v-if="book.author_name"
      type="button"
      @click="emit('search', 'author', book.author_name)"
      class="relative z-10 mt-1 max-w-full self-start truncate text-left text-xs text-muted hover:text-accent dark:text-gray-400 dark:hover:text-emerald-300"
      :title="i18n.t('book_author_search')"
    >{{ book.author_name }}</button>
    <div
      v-if="book.kind === 'comic' || book.category_names?.length"
      class="mt-2 flex flex-wrap gap-1"
    >
      <span
        v-if="book.kind === 'comic'"
        class="rounded-md bg-[#f1e5d8] px-1.5 py-0.5 text-[11px] text-[#704c35] dark:bg-[#473628] dark:text-[#edd7ba]"
      >{{ i18n.t('kind_comics') }}</span>
      <button
        v-for="category in book.category_names.slice(0, 2)"
        :key="category"
        type="button"
        @click="emit('search', 'category', category)"
        class="relative z-10 max-w-full truncate rounded-md bg-accent/10 px-1.5 py-0.5 text-[11px] text-accent hover:bg-accent/20 dark:bg-emerald-300/10 dark:text-emerald-200"
      >{{ category }}</button>
    </div>
    <p v-if="book.description" class="mt-2 line-clamp-2 text-xs leading-5 text-muted dark:text-gray-400">
      {{ book.description }}
    </p>
    <div class="mt-auto flex min-w-0 items-center gap-2 pt-3 text-[11px] text-muted dark:text-gray-400">
      <span v-if="book.status">{{ book.status === 'completed' ? i18n.t('home_completed') : i18n.t('home_ongoing') }}</span>
      <span v-if="sourceName" class="ml-auto min-w-0 truncate" :title="sourceName">{{ sourceName }}</span>
    </div>
  </article>
</template>
