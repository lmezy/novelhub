<script setup lang="ts">
import { ref } from "vue"
import { useRouter } from "vue-router"
import { useAuthStore } from "../stores/auth"

const auth = useAuthStore()
const router = useRouter()
const menuOpen = ref(false)

function logout() {
  auth.logout()
  router.push("/login")
  menuOpen.value = false
}

function closeMenu() {
  menuOpen.value = false
}
</script>

<template>
  <nav class="sticky top-0 z-50 border-b border-border bg-surface/90 backdrop-blur">
    <div class="max-w-6xl mx-auto px-4 h-12 flex items-center justify-between">
      <!-- Logo -->
      <router-link to="/" class="font-bold text-ink no-underline text-sm tracking-tight" @click="closeMenu">
        NovelHub
      </router-link>

      <div class="flex items-center gap-2">
      <button
        @click="auth.toggleDark()"
        class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-sm"
        :title="auth.isDark ? '浅色模式' : '深色模式'"
      >{{ auth.isDark ? '\u2600' : '\u263e' }}</button>

      <!-- Desktop nav -->
      </div>

      <!-- Desktop nav -->
      <div class="hidden sm:flex items-center gap-4">
        <router-link to="/search" class="text-sm text-muted hover:text-ink no-underline transition-colors">搜索</router-link>
        <template v-if="auth.user">
          <router-link v-if="auth.isAdmin" to="/admin" class="text-sm text-muted hover:text-ink no-underline transition-colors">管理</router-link>
          <button @click="logout" class="text-sm text-muted hover:text-ink transition-colors">退出</button>
        </template>
        <template v-else>
          <router-link to="/login" class="text-sm text-muted hover:text-ink no-underline transition-colors">登录</router-link>
        </template>
      </div>

      <!-- Mobile hamburger -->
      <button
        @click="menuOpen = !menuOpen"
        class="sm:hidden w-8 h-8 flex items-center justify-center rounded hover:bg-black/5 transition-colors"
      >
        <span class="text-lg">{{ menuOpen ? "\u2715" : "\u2630" }}</span>
      </button>
    </div>

    <!-- Mobile menu -->
    <div v-if="menuOpen" class="sm:hidden border-t border-border bg-surface px-4 py-3 space-y-2">
      <router-link to="/search" @click="closeMenu" class="block text-sm text-muted hover:text-ink no-underline py-1">搜索</router-link>
      <template v-if="auth.user">
        <router-link v-if="auth.isAdmin" to="/admin" @click="closeMenu" class="block text-sm text-muted hover:text-ink no-underline py-1">管理</router-link>
        <button @click="logout" class="block text-sm text-muted hover:text-ink py-1 w-full text-left">退出</button>
      </template>
      <template v-else>
        <router-link to="/login" @click="closeMenu" class="block text-sm text-muted hover:text-ink no-underline py-1">登录</router-link>
      </template>
    </div>
  </nav>
</template>
