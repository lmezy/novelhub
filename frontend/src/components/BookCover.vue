<script setup lang="ts">
import { ref, watch } from "vue"

const props = defineProps<{
  src?: string | null
  priority?: boolean
}>()

const failed = ref(false)
watch(() => props.src, () => { failed.value = false })
</script>

<template>
  <span class="book-jacket relative isolate block overflow-hidden bg-[#e1e7da] dark:bg-[#29382f]" aria-hidden="true">
    <img
      v-if="src && !failed"
      :src="src"
      alt=""
      :loading="priority ? 'eager' : 'lazy'"
      decoding="async"
      class="absolute inset-0 h-full w-full object-cover"
      @error="failed = true"
    />
    <span v-else class="jacket-pattern absolute inset-0" />
  </span>
</template>

<style scoped>
.jacket-pattern {
  background:
    linear-gradient(90deg, rgb(0 0 0 / 12%) 0 7%, transparent 7%),
    radial-gradient(ellipse at 32% 15%, rgb(255 255 255 / 45%), transparent 70%);
}

.jacket-pattern::after {
  content: "";
  position: absolute;
  inset: 30% 26% auto 27%;
  height: 14%;
  border-block: 1px solid currentColor;
  color: #668072;
  opacity: .48;
}
</style>
