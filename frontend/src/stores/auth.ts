import { defineStore } from "pinia"
import { ref, computed } from "vue"
import { api } from "../api/client"

export interface User {
  id: string
  username: string
  email: string | null
  is_admin: boolean
}

export const useAuthStore = defineStore("auth", () => {
  const user = ref<User | null>(null)
  const token = ref<string | null>(localStorage.getItem("novelhub_token"))

  const isAuthenticated = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.is_admin ?? false)

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

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem("novelhub_token")
  }

  return { user, token, isAuthenticated, isAdmin, login, register, fetchMe, logout }
})
