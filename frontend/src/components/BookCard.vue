<script setup lang="ts">
import type { Book } from "../stores/books"
import { useI18nStore } from "../stores/i18n"

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
  <router-link
    :to="'/books/' + book.id"
    class="group relative block min-w-0 rounded-lg border border-border bg-surface p-3 no-underline transition-all hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-md dark:border-gray-700 dark:bg-gray-900"
  >
    <input
      v-if="selectable"
      type="checkbox"
      :checked="selected"
      @click.prevent.stop="emit('select', book.id)"
      class="absolute left-2 top-2 z-10 h-4 w-4 rounded border-border"
    />
    <button
      @click.prevent.stop="emit('favorite', book)"
      class="absolute right-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded bg-black/55 text-base backdrop-blur-sm"
      :class="book.is_favorite ? 'text-amber-300' : 'text-white hover:text-amber-300'"
      :title="book.is_favorite ? i18n.t('books_favorite_on') : i18n.t('books_favorite_off')"
    >{{ book.is_favorite ? '★' : '☆' }}</button>

    <div v-if="showCover" class="mb-3 aspect-[3/4] w-full overflow-hidden rounded bg-gray-100 dark:bg-gray-800">
      <img v-if="book.cover" :src="book.cover" :alt="book.title" class="h-full w-full object-cover" loading="lazy" />
      <div v-else class="flex h-full items-center justify-center px-3 text-center text-sm font-semibold text-muted dark:text-gray-500">
        {{ book.title }}
      </div>
    </div>

    <h3 class="truncate pr-6 text-sm font-semibold text-ink dark:text-gray-100">{{ book.title }}</h3>
    <button
      v-if="book.author_name"
      @click.prevent.stop="emit('search', 'author', book.author_name)"
      class="mt-1 block max-w-full truncate text-left text-xs text-muted transition-colors hover:text-accent dark:text-gray-400"
      :title="i18n.t('book_author_search')"
    >{{ book.author_name }}</button>
    <div v-if="book.category_names?.length" class="mt-2 flex flex-wrap gap-1">
      <button
        v-for="category in book.category_names.slice(0, 2)"
        :key="category"
        @click.prevent.stop="emit('search', 'category', category)"
        class="rounded bg-accent/10 px-1.5 py-0.5 text-[11px] text-accent"
      >{{ category }}</button>
    </div>
    <p class="mt-2 line-clamp-2 min-h-8 text-xs leading-4 text-muted dark:text-gray-400">
      {{ book.description || i18n.t('home_no_desc') }}
    </p>
    <div class="mt-3 flex items-center gap-2 border-t border-border pt-2 text-[11px] text-muted dark:border-gray-700 dark:text-gray-500">
      <span>{{ book.status === 'completed' ? i18n.t('home_completed') : i18n.t('home_ongoing') }}</span>
      <span v-if="sourceName" class="ml-auto max-w-20 truncate">{{ sourceName }}</span>
    </div>
  </router-link>
</template>
