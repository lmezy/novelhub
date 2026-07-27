const BASE = "/api"

function token(): string | null {
  return localStorage.getItem("novelhub_token")
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  }
  const t = token()
  if (t) {
    headers["Authorization"] = `Bearer ${t}`
  }

  const res = await fetch(`${BASE}${path}`, { ...options, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    let msg: string
    if (Array.isArray(body.detail)) {
      msg = body.detail.map((e: any) => `${e.loc?.join(".") || ""}: ${e.msg}`).join("; ")
    } else if (typeof body.detail === "string") {
      msg = body.detail
    } else {
      msg = `Request failed: ${res.status}`
    }
    throw new Error(msg)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  get<T>(path: string): Promise<T> {
    return request<T>(path)
  },
  post<T>(path: string, data?: unknown): Promise<T> {
    return request<T>(path, { method: "POST", body: data ? JSON.stringify(data) : undefined })
  },
  put<T>(path: string, data?: unknown): Promise<T> {
    return request<T>(path, { method: "PUT", body: data ? JSON.stringify(data) : undefined })
  },
  delete<T>(path: string): Promise<T> {
    return request<T>(path, { method: "DELETE" })
  },
}
