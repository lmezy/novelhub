import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"
import { useI18nStore } from "./i18n"

const USERNAME_RE = /^[A-Za-z0-9]+$/

function passwordStrengthOk(value: string): boolean {
  let categories = 0
  if (/[A-Za-z]/.test(value)) categories++
  if (/\d/.test(value)) categories++
  if (value.includes("_")) categories++
  if (/[^A-Za-z0-9_]/.test(value)) categories++
  return categories >= 2
}

export const useAdminUsersStore = defineStore("adminUsers", () => {
  const i18n = useI18nStore()

  const users = ref<any[]>([])
  const userError = ref("")
  const registrationApprovalEnabled = ref(false)
  const userCreateForm = ref({ username: "", email: "", password: "", role: "user" })
  const showUserCreate = ref(true)
  const userCreating = ref(false)
  const userCreateError = ref("")
  const passwordChanging = ref<Record<string, boolean>>({})

  async function loadUsers() {
    userError.value = ""
    try { users.value = await api.get<any[]>("/admin/users") } catch (e) { userError.value = e instanceof Error ? e.message : i18n.t('admin_failed') }
  }

  async function loadRegistrationApproval() {
    try {
      const res = await api.get<any>("/admin/settings/registration-approval")
      registrationApprovalEnabled.value = res.enabled
    } catch {
      registrationApprovalEnabled.value = false
    }
  }

  async function toggleRegistrationApproval() {
    try {
      const res = await api.put<any>("/admin/settings/registration-approval", {
        enabled: !registrationApprovalEnabled.value,
      })
      registrationApprovalEnabled.value = res.enabled
    } catch (e) {
      alert(e instanceof Error ? e.message : i18n.t('admin_failed'))
    }
  }

  async function createUser() {
    userCreateError.value = ""
    if (!userCreateForm.value.username.trim() || !userCreateForm.value.password) {
      userCreateError.value = i18n.t('admin_create_user_required')
      return
    }
    if (!USERNAME_RE.test(userCreateForm.value.username.trim())) {
      userCreateError.value = i18n.t('admin_username_invalid')
      return
    }
    if (!passwordStrengthOk(userCreateForm.value.password)) {
      userCreateError.value = i18n.t('admin_password_weak')
      return
    }
    userCreating.value = true
    try {
      const body: any = {
        username: userCreateForm.value.username.trim(),
        password: userCreateForm.value.password,
        role: userCreateForm.value.role,
      }
      if (userCreateForm.value.email.trim()) {
        body.email = userCreateForm.value.email.trim()
      }
      await api.post("/admin/users", body)
      await loadUsers()
      userCreateForm.value = { username: "", email: "", password: "", role: "user" }
    } catch (e) {
      userCreateError.value = e instanceof Error ? e.message : i18n.t('admin_failed')
    } finally {
      userCreating.value = false
    }
  }

  async function approveUser(id: string) {
    try {
      await api.put("/admin/users/" + id + "/approve")
      await loadUsers()
    } catch (e) {
      alert(e instanceof Error ? e.message : i18n.t('admin_failed'))
    }
  }

  async function changeUserPassword(u: any) {
    const pwd = prompt(i18n.t('admin_password_prompt'))
    if (!pwd) return
    if (!passwordStrengthOk(pwd)) {
      alert(i18n.t('admin_password_weak'))
      return
    }
    passwordChanging.value[u.id] = true
    try {
      await api.put("/admin/users/" + u.id + "/password", { password: pwd })
      alert(i18n.t('admin_password_changed'))
    } catch (e) {
      alert(e instanceof Error ? e.message : i18n.t('admin_failed'))
    } finally {
      passwordChanging.value[u.id] = false
    }
  }

  async function deleteUser(id: string, username: string) {
    if (!confirm(i18n.t('admin_delete_user_confirm', { username }))) return
    try { await api.delete("/admin/users/" + id); await loadUsers() } catch (e) { alert(e instanceof Error ? e.message : i18n.t('admin_failed')) }
  }

  async function changeUserRole(id: string, role: string) {
    try { await api.put("/admin/users/" + id + "/role", { role }); await loadUsers() } catch (e) { alert(e instanceof Error ? e.message : i18n.t('admin_failed')) }
  }

  async function toggleUserVisibility(
    u: any,
    key: "r18_enabled" | "non_r18_enabled" | "can_manage_visibility",
  ) {
    try {
      const body: any = {}
      body[key] = !u[key]
      await api.put("/admin/users/" + u.id + "/visibility", body)
      await loadUsers()
    } catch (e) {
      alert(e instanceof Error ? e.message : i18n.t('admin_failed'))
    }
  }

  return {
    users,
    userError,
    registrationApprovalEnabled,
    userCreateForm,
    showUserCreate,
    userCreating,
    userCreateError,
    passwordChanging,
    loadUsers,
    loadRegistrationApproval,
    toggleRegistrationApproval,
    createUser,
    approveUser,
    changeUserPassword,
    deleteUser,
    changeUserRole,
    toggleUserVisibility,
  }
})
