/**
 * Server-Sent-Events helper for the AI endpoints.
 *
 * `EventSource` cannot send an Authorization header or a JSON body, so the
 * streamed chat uses `fetch` plus a manual SSE frame parser. Frames look like
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

export async function streamPost(
  path: string,
  body: unknown,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  }
  const t = token()
  if (t) headers["Authorization"] = `Bearer ${t}`

  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body ?? {}),
    signal,
  })

  if (!res.ok) {
    if (res.status === 401 && t) {
      localStorage.removeItem("novelhub_token")
      window.dispatchEvent(new CustomEvent("novelhub:unauthorized"))
    }
    throw new Error(await errorFrom(res))
  }
  if (!res.body) throw new Error("当前浏览器不支持流式响应。")

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  const emit = (frame: string) => {
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

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index = buffer.search(/\r?\n\r?\n/)
    while (index !== -1) {
      const frame = buffer.slice(0, index)
      const separatorLength = buffer[index] === "\r" ? 4 : 2
      buffer = buffer.slice(index + separatorLength)
      emit(frame)
      index = buffer.search(/\r?\n\r?\n/)
    }
  }
  if (buffer.trim()) emit(buffer)
}
