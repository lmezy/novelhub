import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "./i18n"
import { useAuthStore } from "./auth"

export interface Source {
  id: string
  name: string
  url: string | null
  plugin_name: string
  enabled: boolean
  is_r18: boolean
  config?: any
  owner_id?: string | null
  submitter_id?: string | null
  submitter_username?: string | null
  show_contributor?: boolean
  sync_interval_seconds?: number | null
}

export const useAdminSourcesStore = defineStore("adminSources", () => {
  const i18n = useI18nStore()
  const auth = useAuthStore()

  const sources = ref<Source[]>([])
  const sourceForm = ref({ id: "", name: "", url: "", plugin_name: "yuedu", enabled: true, is_r18: false, scope: "personal", sync_interval_seconds: "" })
  const sourceConfigText = ref("")
  const sourceEditingId = ref("")
  const sourceError = ref("")
  /** Row whose inline Cookie/Credential editor is expanded in the list. */
  const expandedSourceId = ref("")

  async function loadSources() {
    sources.value = await api.get<Source[]>("/sources")
  }

  async function deleteSource(id: string) {
    if (!confirm(i18n.t('admin_delete_source_confirm', { id }))) return
    try {
      await api.delete("/sources/" + id)
      await loadSources()
    } catch (e) {
      sourceError.value = e instanceof Error ? e.message : i18n.t('admin_delete_source_failed')
    }
  }

  function parseSourceConfig(value: string): any {
    let parsed: any = JSON.parse(value)
    // Some old source records were serialized twice before being written to
    // JSONB. Unwrap those values so editing does not save a quoted JSON blob.
    for (let i = 0; i < 2 && typeof parsed === "string"; i += 1) {
      parsed = JSON.parse(parsed)
    }
    return parsed
  }

  async function createSource() {
    sourceError.value = ""
    if (!sourceForm.value.name.trim()) {
      sourceError.value = i18n.t('admin_source_name_required')
      return
    }
    let config: any = null
    if (sourceConfigText.value.trim()) {
      try {
        config = parseSourceConfig(sourceConfigText.value)
        if (!config || typeof config !== "object" || Array.isArray(config)) {
          throw new Error("source config must be a JSON object")
        }
      } catch {
        sourceError.value = i18n.t('admin_config_json_invalid')
        return
      }
    }
    // 拉取间隔: how many seconds must pass between two upstream requests.
    // Blank means "not configured" -> the source's own concurrentRate decides.
    const intervalText = String(sourceForm.value.sync_interval_seconds ?? "").trim()
    let syncInterval: number | null = null
    if (intervalText !== "") {
      const parsedInterval = Number(intervalText)
      if (!Number.isFinite(parsedInterval) || parsedInterval < 0 || parsedInterval > 3600) {
        sourceError.value = i18n.t('admin_sync_interval_invalid')
        return
      }
      syncInterval = Math.floor(parsedInterval)
    }
    try {
      const body: any = {
        ...sourceForm.value,
        url: sourceForm.value.url || null,
        config,
        sync_interval_seconds: syncInterval,
      }
      if (sourceEditingId.value) {
        // Editing an existing source must use update semantics. POST-ing the
        // same id that is already in the table is what produced the confusing
        // "Source already exists" (409) when a user simply changed scope.
        // The backend PUT now accepts `scope` and reassigns ownership in place,
        // so a scope change never creates a colliding duplicate row.
        await api.put("/sources/" + sourceEditingId.value, body)
      } else {
        await api.post("/sources", body)
      }
      await loadSources()
      resetSourceForm()
    } catch (e) {
      sourceError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
    }
  }

  function editSource(s: Source) {
    sourceEditingId.value = s.id
    sourceForm.value = {
      id: s.id,
      name: s.name,
      url: s.url || "",
      plugin_name: s.plugin_name || "yuedu",
      enabled: s.enabled,
      is_r18: s.is_r18,
      scope: s.owner_id ? "personal" : "global",
      sync_interval_seconds: s.sync_interval_seconds == null ? "" : String(s.sync_interval_seconds),
    }
    if (!auth.isAdmin && !s.owner_id) {
      sourceForm.value.scope = "personal"
    }
    // Older records may contain the JSONB config as a serialized string.
    // Normalize it before putting it in the editor so editing never produces
    // invalid JSON such as a quoted JSON document.
    if (typeof s.config === "string") {
      try {
        sourceConfigText.value = JSON.stringify(parseSourceConfig(s.config), null, 2)
      } catch {
        sourceConfigText.value = s.config
      }
    } else {
      sourceConfigText.value = s.config ? JSON.stringify(s.config, null, 2) : ""
    }
    window.scrollTo({ top: 0, behavior: "smooth" })
  }

  function resetSourceForm() {
    sourceEditingId.value = ""
    sourceForm.value = { id: "", name: "", url: "", plugin_name: "yuedu", enabled: true, is_r18: false, scope: "personal", sync_interval_seconds: "" }
    sourceConfigText.value = ""
  }

  function canManageSource(s: Source) {
    return auth.isAdmin || s.owner_id === auth.user?.id
  }

  return {
    sources,
    sourceForm,
    sourceConfigText,
    sourceEditingId,
    sourceError,
    expandedSourceId,
    loadSources,
    createSource,
    editSource,
    resetSourceForm,
    deleteSource,
    canManageSource,
  }
})
