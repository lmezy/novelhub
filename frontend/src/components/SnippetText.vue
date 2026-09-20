<script setup lang="ts">
import { computed } from "vue"
import { splitSnippet } from "../utils/snippet"

const props = defineProps<{ text?: string; terms?: string[] }>()

const segments = computed(() => splitSnippet(props.text || "", props.terms || []))
</script>

<template>
  <!--
    One element per segment: `line-clamp-*` on the parent still clamps the whole
    excerpt, and the matched part is marked so the hit is visible on a phone,
    where two lines hold a third of the characters a desktop card shows.
  -->
  <span
    v-for="(segment, index) in segments"
    :key="index"
  ><mark
    v-if="segment.hit"
    class="rounded-sm bg-amber-200 px-0.5 text-amber-900 dark:bg-amber-400/25 dark:text-amber-100"
  >{{ segment.text }}</mark><template v-else>{{ segment.text }}</template></span>
</template>
