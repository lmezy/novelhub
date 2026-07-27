<script setup lang="ts">
import { useAuthStore } from "../stores/auth"
import { useRouter } from "vue-router"

const auth = useAuthStore()
const router = useRouter()

function handleLogout() {
  auth.logout()
  router.push("/")
}
</script>

<template>
  <nav class="border-b border-border bg-surface/80 backdrop-blur sticky top-0 z-50">
    <div class="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
      <div class="flex items-center gap-6">
        <router-link to="/" class="text-lg font-bold tracking-tight text-ink no-underline">
          NovelHub
        </router-link>
        <router-link to="/search" class="text-sm text-muted hover:text-ink transition-colors">
          Search
        </router-link>
      </div>
      <div class="flex items-center gap-3">
        <template v-if="auth.isAuthenticated">
          <span class="text-sm text-muted">{{ auth.user?.username }}</span>
          <router-link
            v-if="auth.isAdmin"
            to="/admin"
            class="text-sm text-muted hover:text-ink transition-colors"
          >Admin</router-link>
          <button
            @click="handleLogout"
            class="text-sm text-muted hover:text-red-600 transition-colors"
          >Logout</button>
        </template>
        <router-link v-else to="/login" class="text-sm text-muted hover:text-ink transition-colors">
          Login
        </router-link>
      </div>
    </div>
  </nav>
</template>
