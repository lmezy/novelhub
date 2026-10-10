/**
 * Server-Sent-Events helper for the streamed endpoints.
 *
 * `EventSource` cannot send an Authorization header or a JSON body, so streams
 * use `fetch` plus a manual SSE frame parser. Frames look like
 * `data: {"type":"delta","text":"..."}\n\n`.
 */

const BASE = "/api"

export interface StreamEvent {
  type: string
  [key: string]: any
}

function token(): string | null {
  return localStorage.getItem("novelhub_token")
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra }
  const t = token()
  if (t) headers["Authorization"] = `Bearer ${t}`
  return headers
}

async function errorFrom(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body?.detail === "string") return body.detail
    if (Array.isArray(body?.detail)) {
      return body.detail.map((e: any) => `${e.loc?.join(".") || ""}: ${e.msg}`).join("; ")
    }
  } catch {
    /* not JSON */
  }
  return `Request failed: ${res.status}`
}

function emitFrame(frame: string, onEvent: (event: StreamEvent) => void) {
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.trim()
    if (!line.startsWith("data:")) continue
    const payload = line.slice(5).trim()
    if (!payload || payload === "[DONE]") continue
    try {
      onEvent(JSON.parse(payload) as StreamEvent)
    } catch {
      /* ignore malformed frame */
    }
  }
}

/** Read an SSE response body frame by frame, in arrival order. */
async function pump(
  res: Response,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  if (!res.body) throw new Error("当前浏览器不支持流式响应。")

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index = buffer.search(/\r?\n\r?\n/)
    while (index !== -1) {
      const frame = buffer.slice(0, index)
      const separatorLength = buffer[index] === "\r" ? 4 : 2
      buffer = buffer.slice(index + separatorLength)
      emitFrame(frame, onEvent)
      index = buffer.search(/\r?\n\r?\n/)
    }
  }
  if (buffer.trim()) emitFrame(buffer, onEvent)
}

async function stream(
  path: string,
  init: RequestInit,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, init)

  if (!res.ok) {
    if (res.status === 401 && token()) {
      localStorage.removeItem("novelhub_token")
      window.dispatchEvent(new CustomEvent("novelhub:unauthorized"))
    }
    throw new Error(await errorFrom(res))
  }
  await pump(res, onEvent)
}

export function streamPost(
  path: string,
  body: unknown,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return stream(
    path,
    {
      method: "POST",
      headers: authHeaders({
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      body: JSON.stringify(body ?? {}),
      signal,
    },
    onEvent,
  )
}

/**
 * The same stream over GET, for endpoints whose word is in the query string
 * (the all-source search). Events arrive in completion order, so a fast site
 * paints before a slow one finishes.
 */
export function streamGet(
  path: string,
  params: Record<string, string> = {},
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const query = new URLSearchParams(params).toString()
  return stream(
    query ? `${path}?${query}` : path,
    { method: "GET", headers: authHeaders({ Accept: "text/event-stream" }), signal },
    onEvent,
  )
}

