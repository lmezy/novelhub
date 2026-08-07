<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { useAuthStore } from "./stores/auth"
import { useI18nStore } from "./stores/i18n"

const auth = useAuthStore()
const i18n = useI18nStore()
const router = useRouter()
const notice = ref("")

onMounted(async () => {
  await auth.fetchMe()
  window.addEventListener("novelhub:unauthorized", () => {
    auth.logout()
    notice.value = i18n.t('auth_required')
    router.push("/login")
  })
})
</script>

<template>
  <p
    v-if="notice"
    class="fixed top-4 left-1/2 -translate-x-1/2 z-[100] px-4 py-2 rounded-lg bg-red-600 text-white text-sm shadow-lg"
  >{{ notice }}</p>
  <router-view />
</template>
