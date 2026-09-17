<script setup lang="ts">
/**
 * Floating AI actions for a text selection inside the reader.
 *
 * The reader renders chapter text with `v-html`, so the component watches the
 * document selection, checks that it lies inside the passed reading container,
 * and shows a small toolbar above it (解释 / 翻译 / 润色 / 续写 / 问 AI).
 * Results are streamed into a card at the bottom of the screen.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { streamPost } from "../api/stream"
import { useI18nStore } from "../stores/i18n"

const props = defineProps<{
  bookId: string
  chapterNumber?: number | null
  container: HTMLElement | null
}>()

const emit = defineEmits<{ (e: "ask", text: string): void }>()

const i18n = useI18nStore()

type Action = "explain" | "translate" | "polish" | "continue"

const MAX_SELECTION = 3000

const visible = ref(false)
const position = ref({ top: 0, left: 0 })
const selectedText = ref("")
const resultOpen = ref(false)
const resultAction = ref<Action>("explain")
const resultText = ref("")
const resultError = ref("")
const resultLoading = ref(false)
const copied = ref(false)
const targetLanguage = ref(i18n.locale === "zh" ? "English" : "中文")
let controller: AbortController | null = null

const actions = computed<{ value: Action; label: string }[]>(() => [
  { value: "explain", label: i18n.t("ai_selection_explain") },
  { value: "translate", label: i18n.t("ai_selection_translate") },
  { value: "polish", label: i18n.t("ai_selection_polish") },
  { value: "continue", label: i18n.t("ai_selection_continue") },
])

function insideContainer(node: Node | null): boolean {
  if (!node || !props.container) return false
  const element = node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement
  return !!element && props.container.contains(element)
}

function hide() {
  visible.value = false
}

function handleSelection() {
  const selection = window.getSelection()
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
    hide()
    return
  }
  const text = selection.toString().trim()
  if (!text || text.length > MAX_SELECTION || !insideContainer(selection.anchorNode)) {
    hide()
    return
  }
  const rect = selection.getRangeAt(0).getBoundingClientRect()
  if (!rect.width && !rect.height) {
    hide()
    return
  }
  selectedText.value = text
  position.value = {
    top: Math.max(8, rect.top - 46),
    left: Math.min(Math.max(rect.left + rect.width / 2, 90), window.innerWidth - 90),
  }
  visible.value = true
}

function scheduleCheck() {
  // Let the browser finish updating the selection first.
  window.setTimeout(handleSelection, 0)
}

async function run(action: Action) {
  hide()
  resultAction.value = action
  resultOpen.value = true
  resultText.value = ""
  resultError.value = ""
  resultLoading.value = true
  copied.value = false
  controller?.abort()
  controller = new AbortController()
  try {
    await streamPost(
      "/ai/transform/stream",
      {
        text: selectedText.value,
        action,
        book_id: props.bookId,
        chapter_number: props.chapterNumber ?? null,
        target_language: targetLanguage.value,
      },
      (event) => {
        if (event.type === "delta") resultText.value += event.text || ""
        else if (event.type === "done" && !resultText.value && event.result) {
          resultText.value = event.result
        } else if (event.type === "error") {
          resultError.value = event.message || i18n.t("ai_selection_failed")
        }
      },
      controller.signal,
    )
  } catch (e) {
    if (!(e instanceof DOMException && e.name === "AbortError")) {
      resultError.value = e instanceof Error ? e.message : i18n.t("ai_selection_failed")
    }
  } finally {
    resultLoading.value = false
    controller = null
  }
}

function askAI() {
  const text = selectedText.value
  hide()
  resultOpen.value = false
  emit("ask", text)
}

function closeResult() {
  controller?.abort()
  controller = null
  resultLoading.value = false
  resultOpen.value = false
}

async function copyResult() {
  try {
    await navigator.clipboard.writeText(resultText.value)
    copied.value = true
    window.setTimeout(() => (copied.value = false), 1500)
  } catch {
    /* clipboard unavailable */
  }
}

function onScroll() {
  if (visible.value) hide()
}

watch(targetLanguage, () => {
  // Changing the target language re-runs the translation with the same text.
  if (resultOpen.value && resultAction.value === "translate") void run("translate")
})

onMounted(() => {
  document.addEventListener("mouseup", scheduleCheck)
  document.addEventListener("touchend", scheduleCheck)
  window.addEventListener("scroll", onScroll, true)
})

onBeforeUnmount(() => {
  document.removeEventListener("mouseup", scheduleCheck)
  document.removeEventListener("touchend", scheduleCheck)
  window.removeEventListener("scroll", onScroll, true)
  controller?.abort()
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed z-[70] -translate-x-1/2 flex items-center gap-0.5 rounded-lg shadow-lg border border-border bg-surface dark:bg-gray-800 px-1 py-1"
      :style="{ top: position.top + 'px', left: position.left + 'px' }"
      @mousedown.prevent
    >
      <button
        v-for="action in actions"
        :key="action.value"
        @click="run(action.value)"
        class="px-2 py-1 text-xs rounded hover:bg-accent/10 hover:text-accent text-ink whitespace-nowrap"
      >{{ action.label }}</button>
      <span class="w-px h-4 bg-border mx-0.5" />
      <button
        @click="askAI"
        class="px-2 py-1 text-xs rounded hover:bg-accent/10 hover:text-accent text-accent font-medium whitespace-nowrap"
      >{{ i18n.t('ai_selection_ask') }}</button>
    </div>

    <div
      v-if="resultOpen"
      class="fixed z-[70] bottom-4 right-4 w-[min(26rem,92vw)] max-h-[60vh] flex flex-col rounded-lg shadow-xl border border-border bg-surface dark:bg-gray-900"
    >
      <div class="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
        <span class="text-xs font-medium text-ink">
          {{ i18n.t('ai_selection_action_' + resultAction) }}
        </span>
        <select
          v-if="resultAction === 'translate'"
          v-model="targetLanguage"
          class="text-[11px] px-1 py-0.5 rounded border border-border bg-paper text-ink"
        >
          <option value="中文">中文</option>
          <option value="English">English</option>
          <option value="日本語">日本語</option>
          <option value="한국어">한국어</option>
        </select>
        <div class="flex-1" />
        <button
          v-if="resultText"
          @click="copyResult"
          class="text-[11px] text-muted hover:text-ink"
        >{{ copied ? i18n.t('ai_selection_copied') : i18n.t('ai_selection_copy') }}</button>
        <button @click="closeResult" class="text-muted hover:text-ink text-lg leading-none">&times;</button>
      </div>

      <div class="flex-1 overflow-y-auto px-3 py-2">
        <p v-if="resultError" class="text-xs text-red-500 break-words">{{ resultError }}</p>
        <p v-else class="text-sm text-ink whitespace-pre-wrap break-words">{{ resultText }}<span v-if="resultLoading" class="animate-pulse">▍</span></p>
      </div>

      <div class="px-3 pb-2 shrink-0">
        <button
          @click="run(resultAction)"
          :disabled="resultLoading"
          class="text-[11px] text-muted hover:text-accent disabled:opacity-40"
        >{{ i18n.t('ai_selection_rerun') }}</button>
      </div>
    </div>
  </Teleport>
</template>
