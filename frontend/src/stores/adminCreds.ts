import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "./i18n"
import { useAdminCookiesStore } from "./adminCookies"

export const useAdminCredsStore = defineStore("adminCreds", () => {
  const i18n = useI18nStore()

  const creds = ref<any[]>([])
  const credForm = ref({ source: "", username: "", password: "" })
  const credError = ref("")
  const credLoggingIn = ref<Record<string, boolean>>({})
  const credLoginResult = ref<Record<string, any>>({})
  const credLoginError = ref<Record<string, string>>({})

  // Manual login state
  const manualLoginActive = ref(false)
  const manualLoginSessionId = ref("")
  const manualLoginScreenshot = ref("")
  const manualLoginSourceName = ref("")
  const manualLoginLoading = ref(false)
  const manualLoginInput = ref("")
  const manualLoginError = ref("")

  function sourceCreds(sourceId: string) {
    return creds.value.filter((c) => c.source === sourceId)
  }

  async function loadCreds() {
    try {
      creds.value = await api.get<any[]>("/credentials")
    } catch { creds.value = [] }
  }

  async function createCred() {
    credError.value = ""
    try {
      await api.post("/credentials", credForm.value)
      await loadCreds()
      credForm.value = { source: "", username: "", password: "" }
    } catch (e) {
      credError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
    }
  }

  async function deleteCred(id: string) {
    await api.delete("/credentials/" + id)
    await loadCreds()
  }

  async function autoLogin(id: string) {
    const cookiesStore = useAdminCookiesStore()
    credLoggingIn.value[id] = true
    credLoginResult.value[id] = null
    credLoginError.value[id] = ""
    try {
      credLoginResult.value[id] = await api.post("/credentials/" + id + "/auto-login")
      await cookiesStore.loadCookies()
    } catch (e) {
      credLoginError.value[id] = e instanceof Error ? e.message : i18n.t('admin_login_failed')
    } finally {
      credLoggingIn.value[id] = false
    }
  }

  async function startManualLogin(credId: string) {
    manualLoginLoading.value = true
    manualLoginError.value = ""
    try {
      const res = await api.post("/credentials/" + credId + "/manual-login/start") as any
      manualLoginSessionId.value = res.session_id
      manualLoginScreenshot.value = res.screenshot
      manualLoginSourceName.value = res.source_name
      manualLoginActive.value = true
      if (res.error) {
        manualLoginError.value = res.error
      }
    } catch (e) {
      manualLoginError.value = e instanceof Error ? e.message : "Failed to start manual login"
    } finally {
      manualLoginLoading.value = false
    }
  }

  function handleManualClick(e: MouseEvent) {
    if (!manualLoginActive.value) return
    const img = e.currentTarget as HTMLImageElement
    const rect = img.getBoundingClientRect()
    const scaleX = img.naturalWidth / rect.width
    const scaleY = img.naturalHeight / rect.height
    const x = Math.round((e.clientX - rect.left) * scaleX)
    const y = Math.round((e.clientY - rect.top) * scaleY)
    manualLoginLoading.value = true
    api.post("/manual-login/" + manualLoginSessionId.value + "/click", { x, y }).then((res: any) => {
      manualLoginScreenshot.value = res.screenshot
    }).catch(e => {
      manualLoginError.value = e instanceof Error ? e.message : i18n.t('admin_click_failed')
    }).finally(() => {
      manualLoginLoading.value = false
    })
  }

  async function handleManualType() {
    if (!manualLoginInput.value) return
    manualLoginLoading.value = true
    try {
      const res = await api.post("/manual-login/" + manualLoginSessionId.value + "/type", { text: manualLoginInput.value }) as any
      manualLoginScreenshot.value = res.screenshot
      manualLoginInput.value = ""
    } catch (e) {
      manualLoginError.value = e instanceof Error ? e.message : i18n.t('admin_type_failed')
    } finally {
      manualLoginLoading.value = false
    }
  }

  async function handleManualKey(key: string) {
    manualLoginLoading.value = true
    try {
      const res = await api.post("/manual-login/" + manualLoginSessionId.value + "/key", { key }) as any
      manualLoginScreenshot.value = res.screenshot
    } catch (e) {
      manualLoginError.value = e instanceof Error ? e.message : i18n.t('admin_key_failed')
    } finally {
      manualLoginLoading.value = false
    }
  }

  async function finishManualLogin() {
    const cookiesStore = useAdminCookiesStore()
    manualLoginLoading.value = true
    try {
      await api.post("/manual-login/" + manualLoginSessionId.value + "/finish")
      manualLoginActive.value = false
      await cookiesStore.loadCookies()
    } catch (e) {
      manualLoginError.value = e instanceof Error ? e.message : i18n.t('admin_cookie_save_failed')
    } finally {
      manualLoginLoading.value = false
    }
  }

  async function cancelManualLogin() {
    try {
      await api.post("/manual-login/" + manualLoginSessionId.value + "/cancel")
    } catch {}
    manualLoginActive.value = false
  }

  return {
    creds,
    credForm,
    credError,
    credLoggingIn,
    credLoginResult,
    credLoginError,
    manualLoginActive,
    manualLoginSessionId,
    manualLoginScreenshot,
    manualLoginSourceName,
    manualLoginLoading,
    manualLoginInput,
    manualLoginError,
    sourceCreds,
    loadCreds,
    createCred,
    deleteCred,
    autoLogin,
    startManualLogin,
    handleManualClick,
    handleManualType,
    handleManualKey,
    finishManualLogin,
    cancelManualLogin,
  }
})
