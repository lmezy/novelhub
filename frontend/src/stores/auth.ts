import { defineStore } from "pinia"
import { ref, computed } from "vue"
import { api } from "../api/client"

export interface User {
  id: string
  username: string
  email: string | null
  role: string
  r18_enabled: boolean
  non_r18_enabled: boolean
  can_manage_visibility: boolean
}

export const useAuthStore = defineStore("auth", () => {
  const user = ref<User | null>(null)
const isDark = ref(localStorage.getItem('novelhub_dark') === 'true')
  const token = ref<string | null>(localStorage.getItem("novelhub_token"))

  const isAuthenticated = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === "admin" || user.value?.role === "super_admin")
const isSuperAdmin = computed(() => user.value?.role === "super_admin")

function toggleDark() {
  isDark.value = !isDark.value
  localStorage.setItem('novelhub_dark', String(isDark.value))
  document.documentElement.classList.toggle('dark', isDark.value)
}

  async function login(username: string, password: string) {
    const res = await api.post<{ access_token: string; user: User }>("/auth/login", {
      username,
      password,
    })
    token.value = res.access_token
    user.value = res.user
    localStorage.setItem("novelhub_token", res.access_token)
  }

  async function register(username: string, password: string, email?: string) {
    const res = await api.post<{ access_token: string; user: User }>("/auth/register", {
      username,
      password,
      email,
    })
    token.value = res.access_token
    user.value = res.user
    localStorage.setItem("novelhub_token", res.access_token)
  }

  async function fetchMe() {
    if (!token.value) return
    try {
      user.value = await api.get<User>("/auth/me")
    } catch {
      logout()
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

 return { user, token, isDark, isAuthenticated, isAdmin, isSuperAdmin, toggleDark, login, register, fetchMe, updateVisibility, logout }
})
