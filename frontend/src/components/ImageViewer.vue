<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue"
import { useI18nStore } from "../stores/i18n"

defineProps<{ src: string; alt: string }>()
const emit = defineEmits<{ close: [] }>()
const i18n = useI18nStore()

const dialog = ref<HTMLDialogElement | null>(null)
const viewport = ref<HTMLElement | null>(null)
const image = ref<HTMLImageElement | null>(null)
const failed = ref(false)
const scale = ref(1)
const offset = ref({ x: 0, y: 0 })
const maxScale = 5
const pointers = new Map<number, { x: number; y: number }>()
let observer: ResizeObserver | undefined

function clampOffset(x: number, y: number) {
  if (!viewport.value || !image.value) return
  const maxX = Math.max(0, (image.value.clientWidth * scale.value - viewport.value.clientWidth) / 2)
  const maxY = Math.max(0, (image.value.clientHeight * scale.value - viewport.value.clientHeight) / 2)
  offset.value = {
    x: Math.min(maxX, Math.max(-maxX, x)),
    y: Math.min(maxY, Math.max(-maxY, y)),
  }
}

function zoom(nextScale: number, clientX?: number, clientY?: number, dx = 0, dy = 0) {
  const bounds = viewport.value?.getBoundingClientRect()
  if (!bounds || failed.value) return
  const next = Math.min(maxScale, Math.max(1, nextScale))
  const ratio = next / scale.value
  const x = clientX === undefined ? 0 : clientX - bounds.left - bounds.width / 2
  const y = clientY === undefined ? 0 : clientY - bounds.top - bounds.height / 2
  scale.value = next
  clampOffset(x + (offset.value.x - x) * ratio + dx, y + (offset.value.y - y) * ratio + dy)
}

function onPointerDown(event: PointerEvent) {
  if (event.button !== 0 || pointers.size >= 2 || failed.value) return
  viewport.value?.setPointerCapture(event.pointerId)
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })
}

function onPointerMove(event: PointerEvent) {
  const previous = pointers.get(event.pointerId)
  if (!previous) return
  const current = { x: event.clientX, y: event.clientY }
  pointers.set(event.pointerId, current)
  const other = Array.from(pointers.entries())
    .find(([id]) => id !== event.pointerId)?.[1]
  const dx = current.x - previous.x
  const dy = current.y - previous.y
  if (other) {
    const oldDistance = Math.hypot(previous.x - other.x, previous.y - other.y)
    const newDistance = Math.hypot(current.x - other.x, current.y - other.y)
    if (oldDistance > 0) {
      zoom(
        scale.value * newDistance / oldDistance,
        (previous.x + other.x) / 2,
        (previous.y + other.y) / 2,
        dx / 2,
        dy / 2,
      )
    }
  } else {
    clampOffset(offset.value.x + dx, offset.value.y + dy)
  }
}

function onPointerEnd(event: PointerEvent) {
  pointers.delete(event.pointerId)
}

function onWheel(event: WheelEvent) {
  const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY
  zoom(scale.value * Math.exp(-delta / 350), event.clientX, event.clientY)
}

function onKeydown(event: KeyboardEvent) {
  const pan: Record<string, [number, number] | undefined> = {
    ArrowLeft: [60, 0], ArrowRight: [-60, 0], ArrowUp: [0, 60], ArrowDown: [0, -60],
  }
  const distance = pan[event.key]
  if (!distance || scale.value === 1) return
  event.preventDefault()
  clampOffset(offset.value.x + distance[0], offset.value.y + distance[1])
}

function fitOffset() {
  clampOffset(offset.value.x, offset.value.y)
}

onMounted(() => {
  dialog.value?.showModal()
  if (viewport.value && image.value) {
    observer = new ResizeObserver(fitOffset)
    observer.observe(viewport.value)
    observer.observe(image.value)
  }
})

onUnmounted(() => observer?.disconnect())
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="image-viewer"
      :aria-label="alt || i18n.t('image_viewer_title')"
      @close="emit('close')"
      @keydown="onKeydown"
    >
      <header class="flex items-center justify-end px-3">
        <button
          type="button"
          class="viewer-control text-2xl"
          :aria-label="i18n.t('reader_close')"
          :title="i18n.t('reader_close')"
          autofocus
          @click="dialog?.close()"
        >&times;</button>
      </header>
      <div
        ref="viewport"
        class="viewer-viewport"
        :class="{ 'cursor-grab active:cursor-grabbing': scale > 1 }"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerEnd"
        @pointercancel="onPointerEnd"
        @lostpointercapture="onPointerEnd"
        @wheel.prevent="onWheel"
        @dblclick="zoom(scale > 1 ? 1 : 2, $event.clientX, $event.clientY)"
      >
        <p v-if="failed" class="p-4 text-center text-sm text-gray-300" role="status">
          {{ i18n.t('image_viewer_failed') }}
        </p>
        <img
          v-else
          ref="image"
          :src="src"
          :alt="alt"
          :style="{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }"
          draggable="false"
          @error="failed = true"
        />
      </div>
      <footer class="flex items-center justify-center gap-2">
        <button
          type="button"
          class="viewer-control text-xl"
          :aria-label="i18n.t('image_viewer_zoom_out')"
          :disabled="failed || scale <= 1"
          @click="zoom(scale / 1.5)"
        >−</button>
        <button
          type="button"
          class="viewer-control min-w-16 text-sm tabular-nums"
          :aria-label="i18n.t('image_viewer_reset')"
          :title="i18n.t('image_viewer_reset')"
          :disabled="failed || scale === 1"
          @click="zoom(1)"
        >{{ Math.round(scale * 100) }}%</button>
        <button
          type="button"
          class="viewer-control text-xl"
          :aria-label="i18n.t('image_viewer_zoom_in')"
          :disabled="failed || scale >= maxScale"
          @click="zoom(scale * 1.5)"
        >+</button>
      </footer>
    </dialog>
  </Teleport>
</template>

<style scoped>
.image-viewer {
  position: fixed;
  inset: 0;
  margin: 0;
  width: 100%;
  height: 100vh;
  height: 100dvh;
  max-width: none;
  max-height: none;
  padding: env(safe-area-inset-top) env(safe-area-inset-right)
    env(safe-area-inset-bottom) env(safe-area-inset-left);
  background: #0c0e12;
  color: #f9fafb;
  grid-template-rows: 3.5rem minmax(0, 1fr) 3.75rem;
  overflow: hidden;
}

.image-viewer[open] {
  display: grid;
}

.viewer-viewport {
  display: grid;
  grid-template: minmax(0, 1fr) / minmax(0, 1fr);
  place-items: center;
  min-width: 0;
  overflow: hidden;
  touch-action: none;
  user-select: none;
}

.viewer-viewport img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  pointer-events: none;
}

.viewer-control {
  border-radius: 0.625rem;
  min-width: 2.75rem;
  min-height: 2.75rem;
  background: rgba(255, 255, 255, 0.1);
}

.viewer-control:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.2);
}

.viewer-control:disabled {
  opacity: 0.5;
}
</style>
