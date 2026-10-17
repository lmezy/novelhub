import { computed, onBeforeUnmount, ref, watch, type Ref } from "vue"

/**
 * Renders long lists in chunks: only the first `chunkSize` items are mounted,
 * and an IntersectionObserver sentinel grows the window as the user scrolls.
 * A 2000-chapter book no longer mounts 2000 DOM nodes at once.
 */
export function useChunkedList<T>(items: Ref<T[]>, chunkSize = 200) {
  const visibleCount = ref(chunkSize)
  const visible = computed(() => items.value.slice(0, visibleCount.value))
  const hasMore = computed(() => visibleCount.value < items.value.length)
  const sentinel = ref<HTMLElement | null>(null)

  let observer: IntersectionObserver | null = null

  watch(sentinel, (el) => {
    observer?.disconnect()
    observer = null
    if (el) {
      observer = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          visibleCount.value += chunkSize
        }
      })
      observer.observe(el)
    }
  })

  // New data (different book / refreshed chapters) starts from the top again.
  watch(items, () => {
    visibleCount.value = chunkSize
  })

  onBeforeUnmount(() => observer?.disconnect())

  return { visible, hasMore, sentinel }
}
