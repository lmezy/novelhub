<script setup lang="ts">
import { onMounted } from "vue"
import { useAuthStore } from "./stores/auth"

const auth = useAuthStore()

onMounted(async () => {
  await auth.fetchMe()
})
</script>

<template>
  <router-view />
</template>
