<script setup lang="ts">
import { nextTick, ref, watch } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "../stores/i18n"

const props = defineProps<{ bookId: string }>()
const i18n = useI18nStore()

interface Message {
  role: "user" | "assistant"
  content: string
}

const messages = ref<Message[]>([])
const input = ref("")
const loading = ref(false)
const container = ref<HTMLElement | null>(null)

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return
  messages.value.push({ role: "user", content: text })
  input.value = ""
  loading.value = true
  await scrollBottom()
  try {
    const res = await api.post<{ answer: string; model: string }>("/ai/chat", {
      book_id: props.bookId,
      message: text,
    })
    messages.value.push({ role: "assistant", content: res.answer })
  } catch (e) {
    messages.value.push({
      role: "assistant",
      content: e instanceof Error ? e.message : i18n.t('ai_request_failed'),
    })
  } finally {
    loading.value = false
    await scrollBottom()
  }
}

async function scrollBottom() {
  await nextTick()
  if (container.value) {
    container.value.scrollTop = container.value.scrollHeight
  }
}
</script>

<template>
  <div class="flex flex-col h-full">
    <div class="text-xs text-muted px-4 py-2 border-b border-border bg-surface/50 shrink-0">
      {{ i18n.t('ai_title') }}
    </div>

    <div ref="container" class="flex-1 overflow-y-auto px-4 py-3 space-y-3">
      <div v-if="messages.length === 0" class="text-xs text-muted text-center py-8">
        {{ i18n.t('ai_empty') }}
      </div>

      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="text-sm"
        :class="msg.role === 'user' ? 'text-right' : ''"
      >
        <div
          class="inline-block max-w-[85%] px-3 py-2 rounded-lg"
          :class="
            msg.role === 'user'
              ? 'bg-accent text-white rounded-br-sm'
              : 'bg-gray-100 dark:bg-gray-800 text-ink rounded-bl-sm'
          "
        >{{ msg.content }}</div>
      </div>

      <div v-if="loading" class="text-xs text-muted">{{ i18n.t('ai_thinking') }}</div>
    </div>

    <div class="px-4 py-3 border-t border-border bg-surface/50 shrink-0">
      <form @submit.prevent="send" class="flex gap-2">
        <input
          v-model="input"
          type="text"
          :placeholder="i18n.t('ai_placeholder')"
          class="flex-1 px-3 py-1.5 rounded border border-border bg-paper text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent/30"
          :disabled="loading"
        />
        <button
          type="submit"
          :disabled="loading || !input.trim()"
          class="px-3 py-1.5 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-40 transition-opacity shrink-0"
        >{{ i18n.t('ai_send') }}</button>
      </form>
    </div>
  </div>
</template>
