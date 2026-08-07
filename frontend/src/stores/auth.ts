import { defineStore } from "pinia"
import { ref, computed } from "vue"
import { api } from "../api/client"

export interface User {
  id: string
  username: string
  nickname?: string | null
  email: string | null
  role: string
  r18_enabled: boolean
  non_r18_enabled: boolean
  can_manage_visibility: boolean
  approved: boolean
  invite_code: string
  invited_by_id: string | null
  settings?: any
}

export const useAuthStore = defineStore("auth", () => {
  const user = ref<User | null>(null)
const isDark = ref(localStorage.getItem('novelhub_dark') === 'true')
  const token = ref<string | null>(localStorage.getItem("novelhub_token"))

  const isAuthenticated = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === "admin" || user.value?.role === "super_admin")
const isSuperAdmin = computed(() => user.value?.role === "super_admin")

function toggleDark() {
  setDark(!isDark.value)
}

function setDark(value: boolean) {
  isDark.value = value
  localStorage.setItem('novelhub_dark', String(isDark.value))
  document.documentElement.classList.toggle('dark', isDark.value)
}

function applyUserSettings(u: User) {
  if (u.settings?.theme === "dark") setDark(true)
  else if (u.settings?.theme === "light") setDark(false)
}

  async function login(username: string, password: string) {
    const res = await api.post<{ access_token: string; user: User }>("/auth/login", {
      username,
      password,
    })
    token.value = res.access_token
    user.value = res.user
    localStorage.setItem("novelhub_token", res.access_token)
    applyUserSettings(res.user)
  }

  async function register(username: string, password: string, email?: string, inviteCode?: string) {
    const res = await api.post<{
      status?: "approved" | "pending"
      access_token?: string
      user: User
    }>("/auth/register", {
      username,
      password,
      email,
      invite_code: inviteCode,
    })
    if (res.access_token) {
      token.value = res.access_token
      user.value = res.user
      localStorage.setItem("novelhub_token", res.access_token)
    }
    if (res.user) applyUserSettings(res.user)
    return res
  }

  async function fetchMe(): Promise<boolean> {
    if (!token.value) return false
    try {
      user.value = await api.get<User>("/auth/me")
      applyUserSettings(user.value)
      return true
    } catch {
      logout()
      return false
    }
  }

  async function updateVisibility(payload: {
    r18_enabled?: boolean
    non_r18_enabled?: boolean
  }) {
    user.value = await api.put<User>("/auth/me/visibility", payload)
  }

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem("novelhub_token")
  }

 return { user, token, isDark, isAuthenticated, isAdmin, isSuperAdmin, toggleDark, setDark, login, register, fetchMe, updateVisibility, logout }
})
