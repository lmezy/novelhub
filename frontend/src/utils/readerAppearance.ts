export type ReaderScript = "original" | "simplified" | "traditional"
export type TextConverter = (text: string) => string

export const readerPalettes = {
  day: { foreground: "#262c28", background: "#faf8f2" },
  paper: { foreground: "#443525", background: "#f5e9d3" },
  sage: { foreground: "#283d31", background: "#e0ebe1" },
  night: { foreground: "#e4e2d9", background: "#151b19" },
}

export type ReaderPalette = keyof typeof readerPalettes | "system" | "custom"

export interface ReaderAppearance {
  fontSize: number
  cnFont: string
  enFont: string
  indent: number
  lineHeight: number
  palette: ReaderPalette
  foreground: string
  background: string
  script: ReaderScript
}

export const defaultReaderAppearance: ReaderAppearance = {
  fontSize: 18,
  cnFont: "default",
  enFont: "default",
  indent: 2,
  lineHeight: 1.9,
  palette: "system",
  ...readerPalettes.day,
  script: "original",
}

export const readerAppearanceKey = "novelhub_reader_appearance"

export function validReaderColor(color: unknown): color is string {
  return typeof color === "string" && /^#[0-9a-f]{6}$/i.test(color)
}

function numberInRange(value: unknown, min: number, max: number, fallback: number) {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(max, Math.max(min, value))
    : fallback
}

export function readReaderAppearance(): ReaderAppearance {
  try {
    const saved = JSON.parse(localStorage.getItem(readerAppearanceKey) || "{}") || {}
    const defaults = defaultReaderAppearance
    return {
      fontSize: numberInRange(saved.fontSize, 14, 26, defaults.fontSize),
      cnFont: saved.cnFont ?? localStorage.getItem("novelhub_cn_font") ?? defaults.cnFont,
      enFont: saved.enFont ?? localStorage.getItem("novelhub_en_font") ?? defaults.enFont,
      indent: numberInRange(saved.indent, 0, 4, defaults.indent),
      lineHeight: numberInRange(saved.lineHeight, 1.5, 2.4, defaults.lineHeight),
      palette: saved.palette === "custom" || Object.hasOwn(readerPalettes, saved.palette)
        ? saved.palette
        : defaults.palette,
      foreground: validReaderColor(saved.foreground) ? saved.foreground : defaults.foreground,
      background: validReaderColor(saved.background) ? saved.background : defaults.background,
      script: saved.script === "simplified" || saved.script === "traditional" ? saved.script : defaults.script,
    }
  } catch {
    return { ...defaultReaderAppearance }
  }
}

export const originalText: TextConverter = (text) => text

const converters: Partial<Record<Exclude<ReaderScript, "original">, TextConverter>> = {}

export async function readerTextConverter(script: ReaderScript): Promise<TextConverter> {
  if (script === "original") return originalText
  if (converters[script]) return converters[script]
  if (script === "simplified") {
    const { Converter } = await import("opencc-js/t2cn")
    return converters[script] = Converter({ from: "tw", to: "cn" })
  }
  const { Converter } = await import("opencc-js/cn2t")
  return converters[script] = Converter({ from: "cn", to: "tw" })
}
