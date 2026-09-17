<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue"
import { api } from "../api/client"
import { streamPost } from "../api/stream"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"

const props = defineProps<{
  bookId: string
  chapterNumber?: number | null
  bookTitle?: string
}>()

const emit = defineEmits<{ (e: "open-settings"): void }>()

const i18n = useI18nStore()
const auth = useAuthStore()

interface Message {
  role: "user" | "assistant"
  content: string
  sources?: Source[]
  mode?: string
  error?: boolean
  streaming?: boolean
}

interface Source {
  chapter_id?: string
  chapter_number?: number | null
  title?: string
  similarity?: number | null
}

type Tab = "chat" | "summary" | "people" | "timeline"

const tab = ref<Tab>("chat")
const status = ref<any>(null)
const statusError = ref("")

const messages = ref<Message[]>([])
const input = ref("")
const loading = ref(false)
const container = ref<HTMLElement | null>(null)
let controller: AbortController | null = null

// Summary
const summaryStart = ref<number | null>(null)
const summaryEnd = ref<number | null>(null)
const summaryStyle = ref("detailed")
const summaryMax = ref(60)
const summaryLoading = ref(false)
const summaryError = ref("")
const summaryResult = ref<any>(null)

// Characters / timeline
const peopleLoading = ref(false)
const peopleError = ref("")
const peopleResult = ref<any>(null)
const timelineLoading = ref(false)
const timelineError = ref("")
const timelineResult = ref<any>(null)

const available = computed(() => status.value?.available === true)
const notReadyReason = computed(() => status.value?.reason || statusError.value)

async function loadStatus() {
  statusError.value = ""
  try {
    status.value = await api.get<any>("/ai/status")
  } catch (e) {
    status.value = null
    statusError.value = e instanceof Error ? e.message : i18n.t("ai_request_failed")
  }
}

function pushUser(text: string) {
  messages.value.push({ role: "user", content: text })
}

async function scrollBottom() {
  await nextTick()
  if (container.value) container.value.scrollTop = container.value.scrollHeight
}

async function send(prefill?: string) {
  const text = (prefill ?? input.value).trim()
  if (!text || loading.value) return
  if (!prefill) input.value = ""
  tab.value = "chat"
  pushUser(text)
  const reply: Message = { role: "assistant", content: "", streaming: true }
  messages.value.push(reply)
  loading.value = true
  await scrollBottom()

  // Drop the placeholder assistant turn *and* the question we just pushed:
  // the backend receives that question separately.
  const history = messages.value
    .slice(0, -2)
    .slice(-10)
    .map((m) => ({ role: m.role, content: m.content }))
    .filter((m) => m.content)

  controller = new AbortController()
  try {
    await streamPost(
      "/ai/chat/stream",
      {
        book_id: props.bookId,
        message: text,
        chapter_number: props.chapterNumber ?? null,
        history,
      },
      (event) => {
        if (event.type === "sources") {
          reply.sources = (event.sources || []) as Source[]
          reply.mode = event.context_mode
        } else if (event.type === "delta") {
          reply.content += event.text || ""
          void scrollBottom()
        } else if (event.type === "done") {
          if (!reply.content && event.answer) reply.content = event.answer
        } else if (event.type === "error") {
          reply.error = true
          reply.content = event.message || i18n.t("ai_request_failed")
        }
      },
      controller.signal,
    )
  } catch (e) {
    if (!(e instanceof DOMException && e.name === "AbortError")) {
      reply.error = true
      reply.content = e instanceof Error ? e.message : i18n.t("ai_request_failed")
    }
  } finally {
    reply.streaming = false
    loading.value = false
    controller = null
    await scrollBottom()
  }
}

function stop() {
  controller?.abort()
  controller = null
  loading.value = false
}

function clearChat() {
  stop()
  messages.value = []
}

async function runSummary() {
  summaryLoading.value = true
  summaryError.value = ""
  summaryResult.value = null
  try {
    const body: any = {
      book_id: props.bookId,
      style: summaryStyle.value,
      max_chapters: Math.max(1, Math.min(240, Number(summaryMax.value) || 60)),
    }
    if (summaryStart.value) body.chapter_start = Number(summaryStart.value)
    if (summaryEnd.value) body.chapter_end = Number(summaryEnd.value)
    summaryResult.value = await api.post<any>("/ai/summary", body)
  } catch (e) {
    summaryError.value = e instanceof Error ? e.message : i18n.t("ai_request_failed")
  } finally {
    summaryLoading.value = false
  }
}

async function runPeople() {
  peopleLoading.value = true
  peopleError.value = ""
  peopleResult.value = null
  try {
    peopleResult.value = await api.post<any>("/ai/person", { book_id: props.bookId })
  } catch (e) {
    peopleError.value = e instanceof Error ? e.message : i18n.t("ai_request_failed")
  } finally {
    peopleLoading.value = false
  }
}

async function runTimeline() {
  timelineLoading.value = true
  timelineError.value = ""
  timelineResult.value = null
  try {
    timelineResult.value = await api.post<any>("/ai/timeline", { book_id: props.bookId })
  } catch (e) {
    timelineError.value = e instanceof Error ? e.message : i18n.t("ai_request_failed")
  } finally {
    timelineLoading.value = false
  }
}

function modeLabel(mode?: string) {
  if (mode === "rag") return i18n.t("ai_mode_rag")
  if (mode === "window") return i18n.t("ai_mode_window")
  if (mode === "head") return i18n.t("ai_mode_head")
  return ""
}

function sourceLabel(source: Source) {
  const title = source.title ? ` ${source.title}` : ""
  return i18n.t("ai_source_chapter", { n: source.chapter_number ?? "?" }) + title
}

function characterRows(): any[] {
  return Array.isArray(peopleResult.value?.characters) ? peopleResult.value.characters : []
}

function eventRows(): any[] {
  return Array.isArray(timelineResult.value?.events) ? timelineResult.value.events : []
}

watch(() => props.bookId, () => {
  clearChat()
  summaryResult.value = null
  peopleResult.value = null
  timelineResult.value = null
  void loadStatus()
})

onMounted(loadStatus)

defineExpose({ ask: (text: string) => send(text) })
</script>

<template>
  <div class="flex flex-col h-full min-h-0">
    <div class="flex items-center gap-1 px-2 py-1.5 border-b border-border bg-surface/60 shrink-0 text-xs">
      <button
        v-for="item in (['chat', 'summary', 'people', 'timeline'] as Tab[])"
        :key="item"
        @click="tab = item"
        class="px-2 py-1 rounded transition-colors"
        :class="tab === item ? 'bg-accent/10 text-accent font-medium' : 'text-muted hover:text-ink'"
      >{{ i18n.t('ai_tab_' + item) }}</button>
      <div class="flex-1" />
      <button
        v-if="tab === 'chat' && messages.length"
        @click="clearChat"
        class="px-2 py-1 rounded text-muted hover:text-ink"
      >{{ i18n.t('ai_clear') }}</button>
    </div>

    <div v-if="!available" class="px-4 py-4 text-xs text-muted space-y-2">
      <p>{{ i18n.t('ai_not_ready') }}</p>
      <p v-if="notReadyReason" class="text-amber-600 dark:text-amber-400 break-words">{{ notReadyReason }}</p>
      <button
        v-if="auth.isAdmin"
        @click="emit('open-settings')"
        class="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium hover:opacity-90"
      >{{ i18n.t('ai_open_settings') }}</button>
    </div>

    <template v-else>
      <!-- Chat -->
      <template v-if="tab === 'chat'">
        <div ref="container" class="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
          <div v-if="messages.length === 0" class="text-xs text-muted text-center py-8">
            {{ i18n.t('ai_empty') }}
          </div>

          <div v-for="(msg, i) in messages" :key="i" class="text-sm">
            <div :class="msg.role === 'user' ? 'text-right' : ''">
              <div
                class="inline-block max-w-[90%] px-3 py-2 rounded-lg whitespace-pre-wrap break-words text-left"
                :class="[
                  msg.role === 'user'
                    ? 'bg-accent text-white rounded-br-sm'
                    : 'bg-gray-100 dark:bg-gray-800 text-ink rounded-bl-sm',
                  msg.error ? 'text-red-600 dark:text-red-400' : '',
                ]"
              >{{ msg.content }}<span v-if="msg.streaming" class="animate-pulse">▍</span></div>
            </div>
            <div v-if="msg.sources && msg.sources.length" class="mt-1.5 flex flex-wrap gap-1">
              <span class="text-[10px] text-muted">{{ modeLabel(msg.mode) }}</span>
              <span
                v-for="(source, si) in msg.sources"
                :key="si"
                class="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent"
              >{{ sourceLabel(source) }}</span>
            </div>
          </div>

          <div v-if="loading && !messages.some((m) => m.streaming)" class="text-xs text-muted">
            {{ i18n.t('ai_thinking') }}
          </div>
        </div>

        <div class="px-3 py-2 border-t border-border bg-surface/60 shrink-0">
          <form @submit.prevent="send()" class="flex gap-2">
            <input
              v-model="input"
              type="text"
              :placeholder="i18n.t('ai_placeholder')"
              class="flex-1 min-w-0 px-3 py-1.5 rounded border border-border bg-paper text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent/30"
              :disabled="loading"
            />
            <button
              v-if="loading"
              type="button"
              @click="stop"
              class="px-3 py-1.5 rounded border border-border text-ink text-sm shrink-0"
            >{{ i18n.t('ai_stop') }}</button>
            <button
              v-else
              type="submit"
              :disabled="!input.trim()"
              class="px-3 py-1.5 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-40 shrink-0"
            >{{ i18n.t('ai_send') }}</button>
          </form>
        </div>
      </template>

      <!-- Summary -->
      <div v-else-if="tab === 'summary'" class="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
        <div class="grid grid-cols-2 gap-2">
          <label class="text-xs text-muted">
            {{ i18n.t('ai_summary_from') }}
            <input v-model.number="summaryStart" type="number" min="1" class="mt-1 w-full px-2 py-1 rounded border border-border bg-paper text-sm text-ink" />
          </label>
          <label class="text-xs text-muted">
            {{ i18n.t('ai_summary_to') }}
            <input v-model.number="summaryEnd" type="number" min="1" class="mt-1 w-full px-2 py-1 rounded border border-border bg-paper text-sm text-ink" />
          </label>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <label class="text-xs text-muted">
            {{ i18n.t('ai_summary_style') }}
            <select v-model="summaryStyle" class="mt-1 w-full px-2 py-1 rounded border border-border bg-paper text-sm text-ink">
              <option value="detailed">{{ i18n.t('ai_summary_style_detailed') }}</option>
              <option value="brief">{{ i18n.t('ai_summary_style_brief') }}</option>
            </select>
          </label>
          <label class="text-xs text-muted">
            {{ i18n.t('ai_summary_max') }}
            <input v-model.number="summaryMax" type="number" min="1" max="240" class="mt-1 w-full px-2 py-1 rounded border border-border bg-paper text-sm text-ink" />
          </label>
        </div>
        <p class="text-[11px] text-muted">{{ i18n.t('ai_summary_hint') }}</p>
        <button
          @click="runSummary"
          :disabled="summaryLoading"
          class="px-3 py-1.5 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >{{ summaryLoading ? i18n.t('ai_summary_running') : i18n.t('ai_summary_run') }}</button>

        <p v-if="summaryError" class="text-xs text-red-500 break-words">{{ summaryError }}</p>

        <div v-if="summaryResult" class="space-y-2">
          <p class="text-[11px] text-muted">
            {{ i18n.t('ai_summary_meta', {
              covered: summaryResult.chapters_covered,
              total: summaryResult.total_chapters,
              model: summaryResult.model,
            }) }}
            <span v-if="summaryResult.sampled">{{ i18n.t('ai_summary_sampled') }}</span>
          </p>
          <div class="text-sm text-ink whitespace-pre-wrap break-words p-3 rounded border border-border bg-surface/50">{{ summaryResult.summary }}</div>
        </div>
      </div>

      <!-- Characters -->
      <div v-else-if="tab === 'people'" class="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
        <button
          @click="runPeople"
          :disabled="peopleLoading"
          class="px-3 py-1.5 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >{{ peopleLoading ? i18n.t('ai_people_running') : i18n.t('ai_people_run') }}</button>
        <p v-if="peopleError" class="text-xs text-red-500 break-words">{{ peopleError }}</p>

        <div v-if="peopleResult" class="space-y-2">
          <p v-if="!characterRows().length" class="text-xs text-muted">{{ i18n.t('ai_people_empty') }}</p>
          <div
            v-for="(person, i) in characterRows()"
            :key="i"
            class="p-2.5 rounded border border-border bg-surface/50 text-sm"
          >
            <div class="flex items-baseline gap-2">
              <span class="font-medium text-ink break-words">{{ person.name || i18n.t('ai_unknown') }}</span>
              <span v-if="person.role" class="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent">{{ person.role }}</span>
              <span v-if="person.first_chapter" class="text-[10px] text-muted">{{ i18n.t('ai_source_chapter', { n: person.first_chapter }) }}</span>
            </div>
            <p v-if="person.description" class="mt-1 text-xs text-muted whitespace-pre-wrap break-words">{{ person.description }}</p>
            <p v-if="person.relationships" class="mt-1 text-xs text-muted whitespace-pre-wrap break-words">{{ person.relationships }}</p>
          </div>
          <details v-if="peopleResult.parsed === false && peopleResult.raw" class="text-xs text-muted">
            <summary class="cursor-pointer">{{ i18n.t('ai_raw_output') }}</summary>
            <pre class="mt-1 whitespace-pre-wrap break-words">{{ peopleResult.raw }}</pre>
          </details>
        </div>
      </div>

      <!-- Timeline -->
      <div v-else class="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
        <button
          @click="runTimeline"
          :disabled="timelineLoading"
          class="px-3 py-1.5 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >{{ timelineLoading ? i18n.t('ai_timeline_running') : i18n.t('ai_timeline_run') }}</button>
        <p v-if="timelineError" class="text-xs text-red-500 break-words">{{ timelineError }}</p>

        <div v-if="timelineResult" class="space-y-2">
          <p v-if="!eventRows().length" class="text-xs text-muted">{{ i18n.t('ai_timeline_empty') }}</p>
          <div
            v-for="(event, i) in eventRows()"
            :key="i"
            class="p-2.5 rounded border border-border bg-surface/50 text-sm"
          >
            <div class="flex items-baseline gap-2">
              <span class="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent shrink-0">
                {{ event.chapter ? i18n.t('ai_source_chapter', { n: event.chapter }) : i18n.t('ai_unknown') }}
              </span>
              <span class="text-ink break-words">{{ event.event }}</span>
            </div>
            <p v-if="event.significance" class="mt-1 text-xs text-muted whitespace-pre-wrap break-words">{{ event.significance }}</p>
          </div>
          <details v-if="timelineResult.parsed === false && timelineResult.raw" class="text-xs text-muted">
            <summary class="cursor-pointer">{{ i18n.t('ai_raw_output') }}</summary>
            <pre class="mt-1 whitespace-pre-wrap break-words">{{ timelineResult.raw }}</pre>
          </details>
        </div>
      </div>
    </template>
  </div>
</template>
