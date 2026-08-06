<script setup lang="ts">
import { ref } from "vue"
import { useRouter } from "vue-router"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import NavBar from "../components/NavBar.vue"

const auth = useAuthStore()
const i18n = useI18nStore()
const router = useRouter()

const isRegister = ref(false)
const username = ref("")
const password = ref("")
const email = ref("")
const error = ref("")
const notice = ref("")
const loading = ref(false)

const USERNAME_RE = /^[A-Za-z0-9]+$/

function passwordStrengthOk(value: string): boolean {
  let categories = 0
  if (/[A-Za-z]/.test(value)) categories++
  if (/\d/.test(value)) categories++
  if (value.includes("_")) categories++
  if (/[^A-Za-z0-9_]/.test(value)) categories++
  return categories >= 2
}

async function submit() {
  if (!username.value || !password.value) {
    error.value = i18n.t('login_required')
    return
  }
  if (isRegister.value) {
    if (!USERNAME_RE.test(username.value)) {
      error.value = i18n.t('login_username_invalid')
      return
    }
    if (!passwordStrengthOk(password.value)) {
      error.value = i18n.t('login_password_weak')
      return
    }
  }
  loading.value = true
  error.value = ""
  notice.value = ""
  try {
    if (isRegister.value) {
      const res = await auth.register(username.value, password.value, email.value || undefined)
      if (res.status === "pending") {
        notice.value = i18n.t('login_pending_approval')
        isRegister.value = false
        username.value = ""
        password.value = ""
        email.value = ""
        return
      }
    } else {
      await auth.login(username.value, password.value)
    }
    router.push("/")
  } catch (e) {
    error.value = e instanceof Error ? e.message : i18n.t('login_failed')
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
        {{ isRegister ? i18n.t('login_title_create') : i18n.t('login_title_login') }}
      </h1>

      <form @submit.prevent="submit" class="space-y-4">
        <div>
          <label class="block text-sm text-muted dark:text-gray-400 mb-1">{{ isRegister ? i18n.t('login_username') : i18n.t('login_username_or_email') }}</label>
          <input
            v-model="username"
            type="text"
            class="w-full px-3 py-2 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-ink placeholder:text-muted dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-accent/30 text-sm"
            :placeholder="isRegister ? i18n.t('login_username_placeholder') : i18n.t('login_username_or_email_placeholder')"
          />
        </div>
        <div v-if="isRegister">
          <label class="block text-sm text-muted dark:text-gray-400 mb-1">{{ i18n.t('login_email_optional') }}</label>
          <input
            v-model="email"
            type="email"
            class="w-full px-3 py-2 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-ink placeholder:text-muted dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-accent/30 text-sm"
            :placeholder="i18n.t('login_email_placeholder')"
          />
        </div>
        <div>
          <label class="block text-sm text-muted dark:text-gray-400 mb-1">{{ i18n.t('login_password') }}</label>
          <input
            v-model="password"
            type="password"
            class="w-full px-3 py-2 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900 text-ink placeholder:text-muted dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-accent/30 text-sm"
            :placeholder="i18n.t('login_password_placeholder')"
          />
        </div>

        <p v-if="error" class="text-sm text-red-600">{{ error }}</p>
        <p v-if="notice" class="text-sm text-green-600">{{ notice }}</p>

        <button
          type="submit"
          :disabled="loading"
          class="w-full py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
        >{{ loading ? i18n.t('login_loading') : isRegister ? i18n.t('login_register') : i18n.t('login_login') }}</button>
      </form>

      <p class="text-sm text-muted dark:text-gray-400 text-center mt-6">
        {{ isRegister ? i18n.t('login_have_account') : i18n.t('login_no_account') }}
        <button
          @click="isRegister = !isRegister; error = ''; notice = ''"
          class="text-accent hover:underline"
        >{{ isRegister ? i18n.t('login_login') : i18n.t('login_register') }}</button>
      </p>
    </main>
  </div>
</template>
