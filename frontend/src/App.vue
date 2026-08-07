<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue"
import { useRouter } from "vue-router"
import { useAuthStore } from "./stores/auth"
import { useI18nStore } from "./stores/i18n"

const auth = useAuthStore()
const i18n = useI18nStore()
const router = useRouter()
const notice = ref("")

const onUnauthorized = () => {
  auth.logout()
  notice.value = i18n.t('auth_required')
  router.push("/login")
}

const onAuthenticated = () => {
  notice.value = ""
}

onMounted(async () => {
  window.addEventListener("novelhub:unauthorized", onUnauthorized)
  window.addEventListener("novelhub:authenticated", onAuthenticated)
  if (auth.token) {
    const ok = await auth.fetchMe()
    if (!ok) {
      notice.value = i18n.t('auth_required')
      router.push("/login")
    }
  }
})

onUnmounted(() => {
  window.removeEventListener("novelhub:unauthorized", onUnauthorized)
  window.removeEventListener("novelhub:authenticated", onAuthenticated)
})
</script>

<template>
  <p
    v-if="notice"
    class="fixed top-4 left-1/2 -translate-x-1/2 z-[100] px-4 py-2 rounded-lg bg-red-600 text-white text-sm shadow-lg"
  >{{ notice }}</p>
  <router-view />
</template>
