<script setup lang="ts">
import { computed, ref } from "vue"
import type { Chapter } from "../stores/books"
import { useI18nStore } from "../stores/i18n"
import { useChunkedList } from "../utils/useChunkedList"

const props = defineProps<{ chapters: Chapter[], displayTitle?: (chapter: Chapter) => string }>()
const i18n = useI18nStore()
const query = ref("")
const matches = computed(() => {
  const term = query.value.trim().toLocaleLowerCase()
  if (!term) return props.chapters
  if (/^\d+$/.test(term)) {
    return props.chapters.filter(chapter => chapter.chapter_number === Number(term))
  }
  return props.chapters.filter(chapter =>
    (chapter.title || "").toLocaleLowerCase().includes(term) ||
    props.displayTitle?.(chapter).toLocaleLowerCase().includes(term)
  )
})
const { visible, sentinel, hasMore } = useChunkedList(matches)
</script>

<template>
  <div>
    <input v-model="query" type="search"
      :aria-label="i18n.t('book_find_chapter')" :placeholder="i18n.t('book_find_chapter')"
      class="mb-3 min-h-10 w-full rounded-lg border border-border bg-paper px-3 text-sm text-ink focus:border-accent dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
    />
    <p v-if="query.trim()" role="status" class="mb-2 text-xs text-muted dark:text-gray-400">
      {{ i18n.t('book_chapter_matches', { n: matches.length }) }}
    </p>
    <div v-if="matches.length" class="divide-y divide-border dark:divide-gray-800">
      <slot v-for="chapter in visible" :key="chapter.id" :chapter="chapter" />
    </div>
    <div v-if="hasMore" ref="sentinel" class="py-3 text-center text-xs text-muted dark:text-gray-400">
      {{ i18n.t('book_chapters_more', { n: matches.length - visible.length }) }}
    </div>
  </div>
</template>
