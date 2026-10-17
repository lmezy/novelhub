import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "./i18n"
import { useAdminSourcesStore } from "./adminSources"

export const useAdminApprovalsStore = defineStore("adminApprovals", () => {
  const i18n = useI18nStore()

  const approvals = ref<any[]>([])
  const approvalError = ref("")
  const approvalReviewing = ref<Record<string, boolean>>({})

  async function loadApprovals() {
    approvalError.value = ""
    try { approvals.value = await api.get<any[]>("/source-changes?status=pending") } catch (e) { approvalError.value = e instanceof Error ? e.message : i18n.t('admin_failed') }
  }

  async function reviewChange(id: string, action: string) {
    const sources = useAdminSourcesStore()
    approvalReviewing.value[id] = true
    try { await api.post("/source-changes/" + id + "/review", { action }); await loadApprovals(); await sources.loadSources() } catch (e) { alert(e instanceof Error ? e.message : i18n.t('admin_failed')) }
    finally { approvalReviewing.value[id] = false }
  }

  return {
    approvals,
    approvalError,
    approvalReviewing,
    loadApprovals,
    reviewChange,
  }
})
