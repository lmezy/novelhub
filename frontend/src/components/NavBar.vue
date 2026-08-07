<script setup lang="ts">
import { ref } from "vue"
import { useRouter } from "vue-router"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"

const auth = useAuthStore()
const i18n = useI18nStore()
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
      <router-link to="/" class="font-bold text-ink no-underline text-sm tracking-tight" @click="closeMenu">
        NovelHub
      </router-link>

      <div class="flex items-center gap-2">
      <button
        @click="i18n.toggleLocale()"
        class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-xs font-medium"
        :title="i18n.isZh ? i18n.t('nav_switch_en') : i18n.t('nav_switch_zh')"
      >{{ i18n.isZh ? 'EN' : '中' }}</button>
      <button
        @click="auth.toggleDark()"
        class="w-7 h-7 flex items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10 transition-colors text-sm"
        :title="auth.isDark ? i18n.t('reader_light_mode') : i18n.t('reader_dark_mode')"
      >{{ auth.isDark ? '\u2600' : '\u263e' }}</button>
      </div>

      <div class="hidden sm:flex items-center gap-4">
        <router-link to="/" class="text-sm text-muted hover:text-ink no-underline transition-colors">{{ i18n.t('nav_shelf') }}</router-link>
        <router-link to="/books" class="text-sm text-muted hover:text-ink no-underline transition-colors">{{ i18n.t('nav_books') }}</router-link>
        <router-link to="/search" class="text-sm text-muted hover:text-ink no-underline transition-colors">{{ i18n.t('nav_search') }}</router-link>
        <template v-if="auth.user">
          <router-link to="/sync" class="text-sm text-muted hover:text-ink no-underline transition-colors">{{ i18n.t('nav_sync') }}</router-link>
          <router-link to="/settings" class="text-sm text-muted hover:text-ink no-underline transition-colors">{{ i18n.t('nav_admin') }}</router-link>
          <button @click="logout" class="text-sm text-muted hover:text-ink transition-colors">{{ i18n.t('nav_logout') }}</button>
        </template>
        <template v-else>
          <router-link to="/login" class="text-sm text-muted hover:text-ink no-underline transition-colors">{{ i18n.t('nav_login') }}</router-link>
        </template>
      </div>

      <button
        @click="menuOpen = !menuOpen"
        class="sm:hidden w-8 h-8 flex items-center justify-center rounded hover:bg-black/5 transition-colors"
      >
        <span class="text-lg">{{ menuOpen ? "\u2715" : "\u2630" }}</span>
      </button>
    </div>

    <div v-if="menuOpen" class="sm:hidden border-t border-border bg-surface px-4 py-3 space-y-2">
      <router-link to="/" @click="closeMenu" class="block text-sm text-muted hover:text-ink no-underline py-1">{{ i18n.t('nav_shelf') }}</router-link>
      <router-link to="/books" @click="closeMenu" class="block text-sm text-muted hover:text-ink no-underline py-1">{{ i18n.t('nav_books') }}</router-link>
      <router-link to="/search" @click="closeMenu" class="block text-sm text-muted hover:text-ink no-underline py-1">{{ i18n.t('nav_search') }}</router-link>
      <template v-if="auth.user">
        <router-link to="/sync" @click="closeMenu" class="block text-sm text-muted hover:text-ink no-underline py-1">{{ i18n.t('nav_sync') }}</router-link>
        <router-link to="/settings" @click="closeMenu" class="block text-sm text-muted hover:text-ink no-underline py-1">{{ i18n.t('nav_admin') }}</router-link>
        <button @click="logout" class="block text-sm text-muted hover:text-ink py-1 w-full text-left">{{ i18n.t('nav_logout') }}</button>
      </template>
      <template v-else>
        <router-link to="/login" @click="closeMenu" class="block text-sm text-muted hover:text-ink no-underline py-1">{{ i18n.t('nav_login') }}</router-link>
      </template>
    </div>
  </nav>
</template>
