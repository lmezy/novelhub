export type ReaderTapAction =
  | "none"
  | "menu"
  | "prev_page"
  | "next_page"
  | "prev_chapter"
  | "next_chapter"

export type ReaderTapRegion =
  | "tl"
  | "tc"
  | "tr"
  | "ml"
  | "mc"
  | "mr"
  | "bl"
  | "bc"
  | "br"

export type ReaderTapActions = Record<ReaderTapRegion, ReaderTapAction>

export const TAP_REGION_KEYS: ReaderTapRegion[] = [
  "tl",
  "tc",
  "tr",
  "ml",
  "mc",
  "mr",
  "bl",
  "bc",
  "br",
]

export const TAP_ACTIONS: ReaderTapAction[] = [
  "none",
  "menu",
  "prev_page",
  "next_page",
  "prev_chapter",
  "next_chapter",
]

export const DEFAULT_TAP_ACTIONS: ReaderTapActions = {
  tl: "prev_page",
  tc: "prev_page",
  tr: "next_page",
  ml: "prev_page",
  mc: "menu",
  mr: "next_page",
  bl: "prev_page",
  bc: "next_page",
  br: "next_page",
}

export function normalizeTapActions(value: unknown): ReaderTapActions {
  const source =
    value && typeof value === "object" ? (value as Record<string, unknown>) : {}
  const actions: ReaderTapActions = { ...DEFAULT_TAP_ACTIONS }
  for (const key of TAP_REGION_KEYS) {
    const raw = source[key]
    if (typeof raw === "string" && (TAP_ACTIONS as string[]).includes(raw)) {
      actions[key] = raw as ReaderTapAction
    }
  }
  if (!TAP_REGION_KEYS.some((key) => actions[key] === "menu")) {
    actions.mc = "menu"
  }
  return actions
}
