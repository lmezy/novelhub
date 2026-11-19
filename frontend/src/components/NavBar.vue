<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"

const auth = useAuthStore()
const i18n = useI18nStore()
const route = useRoute()
const router = useRouter()
const tools = ref<HTMLElement | null>(null)
const toolsButton = ref<HTMLButtonElement | null>(null)
const toolsOpen = ref(false)

const displayName = computed(() => auth.user?.nickname?.trim() || auth.user?.username || "")
const initial = computed(() => Array.from(displayName.value)[0]?.toUpperCase() ?? "N")
const libraryActive = computed(() => ["books", "novels", "comics", "book-detail"].includes(String(route.name)))
const inTools = computed(() => route.name === "settings")
const themeLabel = computed(() => i18n.t(auth.isDark ? "reader_light_mode" : "reader_dark_mode"))
const localeLabel = computed(() => i18n.t(i18n.isZh ? "nav_switch_en" : "nav_switch_zh"))

function closeTools() {
  toolsOpen.value = false
}

function onOutsidePointer(event: PointerEvent) {
  if (event.target instanceof Node && !tools.value?.contains(event.target)) closeTools()
}

function onEscape(event: KeyboardEvent) {
  if (!toolsOpen.value) return
  event.preventDefault()
  closeTools()
  toolsButton.value?.focus()
}

function logout() {
  closeTools()
  auth.logout()
  void router.push("/login")
}

watch(() => route.fullPath, closeTools)
onMounted(() => document.addEventListener("pointerdown", onOutsidePointer))
onUnmounted(() => document.removeEventListener("pointerdown", onOutsidePointer))
</script>

<template>
  <header class="site-header sticky top-0 z-50">
    <div class="header-grid mx-auto max-w-6xl px-4 sm:px-6">
      <router-link :to="auth.user ? '/' : '/login'" class="brand-link" aria-label="NovelHub">
        <span class="brand-mark" aria-hidden="true"><span></span><span></span></span>
        <span>NovelHub</span>
      </router-link>

      <nav v-if="auth.user" class="primary-nav" :aria-label="i18n.t('nav_primary')">
        <router-link to="/" :class="{ selected: route.name === 'home' }">{{ i18n.t('nav_shelf') }}</router-link>
        <router-link to="/books" :class="{ selected: libraryActive }">{{ i18n.t('nav_library') }}</router-link>
        <router-link to="/discover" :class="{ selected: route.name === 'discover' }">{{ i18n.t('nav_discover') }}</router-link>
        <router-link to="/sync" :class="{ selected: route.name === 'sync' }">{{ i18n.t('nav_sync') }}</router-link>
      </nav>

      <div v-if="auth.user" ref="tools" class="header-actions" @keydown.esc="onEscape">
        <button
          ref="toolsButton"
          type="button"
          class="tools-toggle"
          :class="{ selected: inTools || toolsOpen }"
          :aria-expanded="toolsOpen"
          aria-controls="site-tools"
          @click="toolsOpen = !toolsOpen"
        >
          <span class="account-initial" aria-hidden="true">{{ initial }}</span>
          {{ i18n.t('nav_tools') }}
          <span class="chevron" aria-hidden="true"></span>
        </button>

        <div v-if="toolsOpen" id="site-tools" class="tools-panel">
          <div class="account-row">
            <span class="min-w-0 truncate text-sm font-semibold" :title="displayName">{{ displayName }}</span>
            <button type="button" class="display-toggle" :aria-label="localeLabel" :title="localeLabel" @click="i18n.toggleLocale()">
              {{ i18n.isZh ? 'EN' : '中' }}
            </button>
            <button type="button" class="display-toggle" :aria-label="themeLabel" :title="themeLabel" @click="auth.toggleDark()">
              <span aria-hidden="true" class="text-base">{{ auth.isDark ? '☀' : '☾' }}</span>
            </button>
          </div>

          <div class="tool-group">
            <p>{{ i18n.t('nav_library_tools') }}</p>
            <router-link :to="{ name: 'settings', query: { tab: 'sources' } }" @click="closeTools">{{ i18n.t('nav_sources') }}</router-link>
            <router-link v-if="auth.isAdmin" :to="{ name: 'settings', query: { tab: 'add' } }" @click="closeTools">{{ i18n.t('nav_add_books') }}</router-link>
          </div>

          <div class="tool-group">
            <p>{{ i18n.t('nav_my_account') }}</p>
            <router-link :to="{ name: 'settings', query: { tab: 'prefs' } }" @click="closeTools">{{ i18n.t('nav_admin') }}</router-link>
            <button type="button" @click="logout">{{ i18n.t('nav_logout') }}</button>
          </div>
        </div>
      </div>

      <div v-else class="header-actions unauthenticated-actions">
        <button type="button" class="display-toggle" :aria-label="localeLabel" :title="localeLabel" @click="i18n.toggleLocale()">
          {{ i18n.isZh ? 'EN' : '中' }}
        </button>
        <button type="button" class="display-toggle" :aria-label="themeLabel" :title="themeLabel" @click="auth.toggleDark()">
          <span aria-hidden="true" class="text-base">{{ auth.isDark ? '☀' : '☾' }}</span>
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.site-header {
  --header-surface: #fffefa;
  --header-ink: #202924;
  --header-muted: #626d66;
  --header-border: #dedfd5;
  --header-selected: #e7f0e9;
  --header-accent: #216252;
  color: var(--header-ink);
  border-bottom: 1px solid var(--header-border);
  background: var(--header-surface);
  background: color-mix(in srgb, var(--header-surface) 95%, transparent);
  backdrop-filter: blur(14px);
}

:global(.dark) .site-header {
  --header-surface: #101816;
  --header-ink: #f2f6ef;
  --header-muted: #aebeb5;
  --header-border: #33413a;
  --header-selected: #243b33;
  --header-accent: #9dd9c1;
}

.header-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "brand actions" "nav nav";
  align-items: center;
}

.brand-link {
  grid-area: brand;
  justify-self: start;
  display: inline-flex;
  align-items: center;
  gap: 0.625rem;
  min-height: 3.55rem;
  font-size: 1.12rem;
  font-weight: 750;
  letter-spacing: -0.045em;
}

.brand-mark {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 2px;
  align-items: center;
  width: 2rem;
  height: 2rem;
  padding: 0.5rem 0.44rem;
  border-radius: 0.55rem;
  background: #245e51;
  color: #ecf5e9;
}

.brand-mark > span {
  height: 0.82rem;
  border: 1px solid currentColor;
  border-radius: 0.125rem 0.28rem 0.125rem 0.125rem;
  transform: skewY(9deg);
}

.brand-mark > span + span {
  border-radius: 0.28rem 0.125rem 0.125rem 0.125rem;
  transform: skewY(-9deg);
}

.primary-nav {
  grid-area: nav;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: 0.2rem;
  padding-bottom: 0.38rem;
}

.primary-nav a,
.tools-toggle,
.display-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 2.6rem;
  padding: 0.35rem 0.58rem;
  border-radius: 0.65rem;
  color: var(--header-muted);
  font-size: 0.875rem;
  font-weight: 600;
  transition: background-color 150ms ease, color 150ms ease;
}

.primary-nav a:hover,
.primary-nav a.selected,
.tools-toggle:hover,
.tools-toggle.selected,
.display-toggle:hover {
  background: var(--header-selected);
  color: var(--header-accent);
}

.header-actions {
  position: relative;
  grid-area: actions;
  justify-self: end;
}

.unauthenticated-actions { display: flex; align-items: center; gap: 0.25rem; }
.tools-toggle { gap: 0.45rem; white-space: nowrap; }
.display-toggle { width: 2.45rem; min-height: 2.45rem; padding: 0; }

.account-initial {
  display: grid;
  width: 1.6rem;
  height: 1.6rem;
  place-items: center;
  border-radius: 50%;
  background: var(--header-selected);
  color: var(--header-accent);
  font-size: 0.75rem;
  font-weight: 700;
}

.chevron {
  width: 0.36rem;
  height: 0.36rem;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: translateY(-2px) rotate(45deg);
}

.tools-panel {
  position: absolute;
  top: calc(100% + 0.5rem);
  right: 0;
  width: min(18rem, calc(100vw - 2rem));
  max-height: calc(100vh - 5rem);
  max-height: calc(100dvh - 5rem);
  overflow-y: auto;
  padding: 0.48rem;
  border: 1px solid var(--header-border);
  border-radius: 0.95rem;
  background: var(--header-surface);
  box-shadow: 0 16px 48px #0c302630, 0 2px 6px #0c302616;
}

.account-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 0.15rem;
  padding: 0.15rem 0.25rem 0.35rem 0.65rem;
}

.tool-group { border-top: 1px solid var(--header-border); padding-block: 0.37rem; }
.tool-group > p { padding: 0.25rem 0.65rem 0.15rem; color: var(--header-muted); font-size: 0.75rem; }
.tool-group > :is(a, button) {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 2.62rem;
  padding: 0.4rem 0.65rem;
  border-radius: 0.5rem;
  text-align: start;
  font-size: 0.875rem;
  font-weight: 550;
}

.tool-group > :is(a, button):hover { background: var(--header-selected); color: var(--header-accent); }
.site-header :is(a, button):focus-visible { outline: 2px solid var(--header-accent); outline-offset: 2px; }

@media (min-width: 640px) {
  .header-grid { grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); grid-template-areas: "brand nav actions"; min-height: 4.15rem; }
  .primary-nav { padding: 0; grid-auto-columns: auto; }
  .primary-nav a { padding-inline: 1.05rem; }
}
</style>
