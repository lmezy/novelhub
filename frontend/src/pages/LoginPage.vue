<script setup lang="ts">
import { ref } from "vue"
import { useRouter } from "vue-router"
import { useAuthStore } from "../stores/auth"
import NavBar from "../components/NavBar.vue"

const auth = useAuthStore()
const router = useRouter()

const isRegister = ref(false)
const username = ref("")
const password = ref("")
const email = ref("")
const error = ref("")
const loading = ref(false)

async function submit() {
  if (!username.value || !password.value) {
    error.value = "Username and password are required"
    return
  }
  loading.value = true
  error.value = ""
  try {
    if (isRegister.value) {
      await auth.register(username.value, password.value, email.value || undefined)
    } else {
      await auth.login(username.value, password.value)
    }
    router.push("/")
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Authentication failed"
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-800 dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="max-w-sm mx-auto px-4 py-16">
      <h1 class="text-2xl font-bold mb-6 text-center">
        {{ isRegister ? 'Create Account' : 'Login' }}
      </h1>

      <form @submit.prevent="submit" class="space-y-4">
        <div>
          <label class="block text-sm text-muted dark:text-gray-400 mb-1">Username</label>
          <input
            v-model="username"
            type="text"
            class="w-full px-3 py-2 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-ink placeholder:text-muted dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-accent/30 text-sm"
            placeholder="Username"
          />
        </div>
        <div v-if="isRegister">
          <label class="block text-sm text-muted dark:text-gray-400 mb-1">Email (optional)</label>
          <input
            v-model="email"
            type="email"
            class="w-full px-3 py-2 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-ink placeholder:text-muted dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-accent/30 text-sm"
            placeholder="email@example.com"
          />
        </div>
        <div>
          <label class="block text-sm text-muted dark:text-gray-400 mb-1">Password</label>
          <input
            v-model="password"
            type="password"
            class="w-full px-3 py-2 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-ink placeholder:text-muted dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-accent/30 text-sm"
            placeholder="Password"
          />
        </div>

        <p v-if="error" class="text-sm text-red-600">{{ error }}</p>

        <button
          type="submit"
          :disabled="loading"
          class="w-full py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
        >{{ loading ? 'Please wait...' : isRegister ? 'Register' : 'Login' }}</button>
      </form>

      <p class="text-sm text-muted dark:text-gray-400 text-center mt-6">
        {{ isRegister ? 'Already have an account?' : "Don't have an account?" }}
        <button
          @click="isRegister = !isRegister; error = ''"
          class="text-accent hover:underline"
        >{{ isRegister ? 'Login' : 'Register' }}</button>
      </p>
    </main>
  </div>
</template>
