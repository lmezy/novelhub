import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "./i18n"

export interface CookieItem {
  id: string
  source: string
  cookie_data: string
  expired_at: string | null
  created_at?: string | null
  updated_at?: string | null
}

export const useAdminCookiesStore = defineStore("adminCookies", () => {
  const i18n = useI18nStore()

  const cookies = ref<CookieItem[]>([])
  const cookieForm = ref({ source: "", cookie_data: "", expired_at: "" })
  const cookieEditingId = ref("")
  const cookieError = ref("")
  const cookieTesting = ref(false)
  const cookieTestResult = ref<any>(null)
  const cookieTestError = ref("")

  /** ``datetime-local`` wants ``YYYY-MM-DDTHH:mm``. */
  function toDateTimeLocal(value: string | null | undefined) {
    return value ? String(value).slice(0, 16) : ""
  }

  function sourceCookies(sourceId: string) {
    return cookies.value.filter((c) => c.source === sourceId)
  }

  function cookieExpired(c: CookieItem) {
    return !!c.expired_at && new Date(c.expired_at) < new Date()
  }

  /** When the stored Cookie was last written; ``created_at`` is only the fallback
   *  for rows that predate the ``updated_at`` column. */
  function cookieWrittenAt(c: CookieItem) {
    return c.updated_at || c.created_at || ""
  }

  async function testCookie() {
    cookieTestError.value = ""
    cookieTestResult.value = null
    cookieTesting.value = true
    try {
      cookieTestResult.value = await api.post("/cookies/test", {
        source: cookieForm.value.source,
        cookie_data: cookieForm.value.cookie_data,
      })
    } catch (e) {
      cookieTestError.value = e instanceof Error ? e.message : i18n.t('admin_test_failed')
    } finally {
      cookieTesting.value = false
    }
  }

  async function loadCookies() {
    cookies.value = await api.get<CookieItem[]>("/cookies")
  }

  async function createCookie() {
    cookieError.value = ""
    try {
      const body: any = { source: cookieForm.value.source, cookie_data: cookieForm.value.cookie_data }
      if (cookieForm.value.expired_at) body.expired_at = cookieForm.value.expired_at
      if (cookieEditingId.value) {
        const updateBody: any = {
          expired_at: cookieForm.value.expired_at || null,
        }
        if (cookieForm.value.cookie_data.trim()) {
          updateBody.cookie_data = cookieForm.value.cookie_data
        }
        await api.put("/cookies/" + cookieEditingId.value, updateBody)
      } else {
        await api.post("/cookies", body)
      }
      await loadCookies()
      cookieEditingId.value = ""
      cookieForm.value = { source: "", cookie_data: "", expired_at: "" }
    } catch (e) {
      cookieError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
    }
  }

  function editCookie(c: CookieItem) {
    cookieEditingId.value = c.id
    cookieForm.value = {
      source: c.source,
      cookie_data: "",
      expired_at: toDateTimeLocal(c.expired_at),
    }
    window.scrollTo({ top: 0, behavior: "smooth" })
  }

  async function deleteCookie(id: string) {
    await api.delete("/cookies/" + id)
    await loadCookies()
  }

  return {
    cookies,
    cookieForm,
    cookieEditingId,
    cookieError,
    cookieTesting,
    cookieTestResult,
    cookieTestError,
    toDateTimeLocal,
    sourceCookies,
    cookieExpired,
    cookieWrittenAt,
    testCookie,
    loadCookies,
    createCookie,
    editCookie,
    deleteCookie,
  }
})
