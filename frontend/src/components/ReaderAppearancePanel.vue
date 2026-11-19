<script setup lang="ts">
import { computed, ref } from "vue"
import { useI18nStore } from "../stores/i18n"
import {
  defaultReaderAppearance,
  readerPalettes,
  validReaderColor,
  type ReaderAppearance,
  type ReaderScript,
} from "../utils/readerAppearance"

const props = defineProps<{
  systemDark: boolean
  cnFonts: { value: string; label: string }[]
  enFonts: { value: string; label: string }[]
  scriptLoading: boolean
  scriptFailed: boolean
}>()
const emit = defineEmits<{ retry: [] }>()
const appearance = defineModel<ReaderAppearance>({ required: true })
const i18n = useI18nStore()
const section = ref<"text" | "colors">("text")
const scriptOptions: ReaderScript[] = ["original", "simplified", "traditional"]

const palettes = computed(() => ({
  system: props.systemDark ? readerPalettes.night : readerPalettes.day,
  ...readerPalettes,
  custom: { foreground: appearance.value.foreground, background: appearance.value.background },
}))
const colors = computed(() => palettes.value[appearance.value.palette])

function setColor(key: "foreground" | "background", event: Event) {
  const input = event.target as HTMLInputElement
  if (!validReaderColor(input.value)) {
    input.value = colors.value[key]
    return
  }
  const { foreground, background } = colors.value
  appearance.value = { ...appearance.value, palette: "custom", foreground, background, [key]: input.value }
}
</script>

<template>
  <section class="appearance-panel" :aria-label="i18n.t('reader_appearance')">
    <div class="appearance-heading">
      <div class="appearance-sections" role="group" :aria-label="i18n.t('reader_appearance')">
        <button type="button" :aria-pressed="section === 'text'" @click="section = 'text'">
          {{ i18n.t('reader_appearance_text') }}
        </button>
        <button type="button" :aria-pressed="section === 'colors'" @click="section = 'colors'">
          {{ i18n.t('reader_appearance_colors') }}
        </button>
      </div>
      <button type="button" class="appearance-reset" @click="appearance = { ...defaultReaderAppearance }">
        {{ i18n.t('reader_appearance_reset') }}
      </button>
    </div>

    <div v-if="section === 'text'" class="appearance-fields">
      <div class="appearance-sliders">
        <label>
          <span>{{ i18n.t('reader_font_size') }} <output>{{ appearance.fontSize }} px</output></span>
          <input type="range" min="14" max="26" step="1" v-model.number="appearance.fontSize" :aria-label="i18n.t('reader_font_size')" />
        </label>
        <label>
          <span>{{ i18n.t('reader_indent') }} <output>{{ appearance.indent }} em</output></span>
          <input type="range" min="0" max="4" step="0.5" v-model.number="appearance.indent" :aria-label="i18n.t('reader_indent')" />
        </label>
        <label>
          <span>{{ i18n.t('reader_line_height') }} <output>{{ appearance.lineHeight.toFixed(1) }}</output></span>
          <input type="range" min="1.5" max="2.4" step="0.1" v-model.number="appearance.lineHeight" :aria-label="i18n.t('reader_line_height')" />
        </label>
      </div>

      <div class="appearance-fonts">
        <label>{{ i18n.t('reader_cn_font') }}
          <select v-model="appearance.cnFont">
            <option v-for="font in cnFonts" :key="font.value" :value="font.value">{{ font.label }}</option>
          </select>
        </label>
        <label>{{ i18n.t('reader_en_font') }}
          <select v-model="appearance.enFont">
            <option v-for="font in enFonts" :key="font.value" :value="font.value">{{ font.label }}</option>
          </select>
        </label>
      </div>

      <fieldset class="appearance-script">
        <legend>{{ i18n.t('reader_script') }}</legend>
        <div class="appearance-script-options">
          <button v-for="script in scriptOptions" :key="script" type="button"
            :aria-pressed="appearance.script === script" @click="appearance.script = script">
            {{ i18n.t(`reader_script_${script}`) }}
          </button>
        </div>
        <p v-if="scriptLoading" class="appearance-status" role="status">{{ i18n.t('reader_script_loading') }}</p>
        <p v-else-if="scriptFailed" class="appearance-status" role="status">
          {{ i18n.t('reader_script_failed') }}
          <button type="button" class="appearance-reset" @click="emit('retry')">{{ i18n.t('reader_appearance_retry') }}</button>
        </p>
      </fieldset>
    </div>

    <div v-else class="appearance-fields">
      <div class="appearance-palettes">
        <button v-for="(palette, name) in palettes" :key="name" type="button"
          :aria-pressed="appearance.palette === name" @click="appearance.palette = name">
          <span aria-hidden="true" :style="{ background: palette.background, color: palette.foreground }">Aa</span>
          {{ i18n.t(`reader_palette_${name}`) }}
        </button>
      </div>
      <div class="appearance-colors">
        <div v-for="key of (['foreground', 'background'] as const)" :key="key" class="appearance-color-field">
          <label class="appearance-color-label">
            {{ i18n.t(`reader_color_${key}`) }}
            <input class="appearance-color-swatch" type="color" :value="colors[key]" @input="setColor(key, $event)" />
          </label>
          <input class="appearance-color-value" type="text" spellcheck="false" autocomplete="off" maxlength="7"
            :aria-label="i18n.t(`reader_color_${key}_hex`)" :value="colors[key]" @change="setColor(key, $event)" />
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.appearance-panel {
  --appearance-border: color-mix(in srgb, currentColor 18%, transparent);
  --appearance-tint: color-mix(in srgb, currentColor 6%, transparent);
  --appearance-active: #276454;
  display: grid;
  gap: 1rem;
  text-align: start;
  font: 500 0.82rem/1.35 system-ui, sans-serif;
}

:global(.dark) .appearance-panel { --appearance-active: #94cbb6; }
.appearance-heading { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
.appearance-sections, .appearance-script-options { display: flex; flex-wrap: wrap; gap: 0.35rem; }
.appearance-sections button, .appearance-script-options button {
  min-height: 2.5rem;
  padding: 0.35rem 0.75rem;
  border-radius: 0.65rem;
  border: 1px solid var(--appearance-border);
}

.appearance-panel button[aria-pressed="true"] {
  border-color: var(--appearance-active);
  color: var(--appearance-active);
  font-weight: 700;
  background: color-mix(in srgb, var(--appearance-active) 10%, transparent);
}

.appearance-panel button:hover { background: var(--appearance-tint); }
.appearance-reset { color: var(--appearance-active); padding: 0.45rem 0.5rem; border-radius: 0.4rem; white-space: nowrap; }
.appearance-fields, .appearance-sliders { display: grid; gap: 0.8rem; }
.appearance-fields label { display: grid; gap: 0.35rem; }
.appearance-sliders label span { display: flex; justify-content: space-between; gap: 0.5rem; }
.appearance-sliders output { opacity: 0.68; font-variant-numeric: tabular-nums; }
.appearance-panel input[type="range"] { width: 100%; min-height: 1.2rem; accent-color: var(--appearance-active); }
.appearance-fonts, .appearance-colors { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.6rem; }
.appearance-panel select, .appearance-color-value {
  min-width: 0;
  min-height: 2.5rem;
  padding: 0.35rem 0.5rem;
  border: 1px solid var(--appearance-border);
  border-radius: 0.55rem;
  background: var(--appearance-tint);
}

:global(.dark) .appearance-panel select option { background: #22242a; color: #f1f1f1; }
.appearance-script legend { margin-bottom: 0.35rem; }
.appearance-status { margin-top: 0.5rem; font-size: 0.75rem; }
.appearance-palettes { display: grid; grid-template-columns: repeat(auto-fit, minmax(4rem, 1fr)); gap: 0.35rem; }
.appearance-palettes button {
  display: grid;
  justify-items: center;
  gap: 0.3rem;
  min-width: 0;
  padding: 0.4rem 0.3rem;
  border: 1px solid var(--appearance-border);
  border-radius: 0.55rem;
  font-size: 0.72rem;
}

.appearance-palettes span {
  display: grid;
  place-items: center;
  width: 100%;
  height: 2rem;
  border-radius: 0.3rem;
  font: 700 1.05rem/1 Georgia, serif;
}

.appearance-color-field { display: grid; gap: 0.35rem; padding: 0.5rem; border: 1px solid var(--appearance-border); border-radius: 0.65rem; }
.appearance-fields .appearance-color-label { display: flex; justify-content: space-between; align-items: center; }
.appearance-color-swatch { width: 2rem; height: 2rem; cursor: pointer; background: transparent; }
.appearance-color-value { width: 100%; font: 500 0.8rem ui-monospace, monospace; text-transform: uppercase; }
</style>
