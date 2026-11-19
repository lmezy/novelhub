<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import { useAuthStore } from "../stores/auth"
import { useI18nStore } from "../stores/i18n"
import NavBar from "../components/NavBar.vue"

const auth = useAuthStore()
const i18n = useI18nStore()
const router = useRouter()
const route = useRoute()

const isRegister = ref(false)
const username = ref("")
const password = ref("")
const email = ref("")
const inviteCode = ref("")
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
    if (!inviteCode.value.trim()) {
      error.value = i18n.t('login_invite_required')
      return
    }
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
      const res = await auth.register(
        username.value,
        password.value,
        email.value || undefined,
        inviteCode.value.trim(),
      )
      if (res.status === "pending") {
        notice.value = i18n.t('login_pending_approval')
        isRegister.value = false
        username.value = ""
        password.value = ""
        email.value = ""
        inviteCode.value = ""
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

onMounted(() => {
  const invite = route.query.invite
  if (typeof invite === "string" && invite) {
    inviteCode.value = invite
    isRegister.value = true
  }
})
</script>

<template>
  <div class="min-h-screen bg-paper dark:bg-gray-950 dark:text-gray-100">
    <NavBar />

    <main class="mx-auto max-w-md px-4 py-9 sm:py-16">
      <section class="rounded-3xl border border-border bg-surface px-5 py-7 shadow-sm dark:border-gray-800 dark:bg-gray-900 sm:px-8 sm:py-9">
        <h1 class="mb-6 text-2xl font-semibold tracking-tight sm:text-3xl">
          {{ isRegister ? i18n.t('login_title_create') : i18n.t('login_title_login') }}
        </h1>

        <form @submit.prevent="submit" class="space-y-4">
          <div>
            <label for="login-username" class="login-label">{{ isRegister ? i18n.t('login_username') : i18n.t('login_username_or_email') }}</label>
            <input
              id="login-username"
              v-model="username"
              type="text"
              autocomplete="username"
              autocapitalize="none"
              class="login-input"
              :placeholder="isRegister ? i18n.t('login_username_placeholder') : i18n.t('login_username_or_email_placeholder')"
            />
          </div>
          <div v-if="isRegister">
            <label for="login-email" class="login-label">{{ i18n.t('login_email_optional') }}</label>
            <input
              id="login-email"
              v-model="email"
              type="email"
              autocomplete="email"
              class="login-input"
              :placeholder="i18n.t('login_email_placeholder')"
            />
          </div>
          <div v-if="isRegister">
            <label for="login-invite" class="login-label">{{ i18n.t('login_invite_code') }}</label>
            <input
              id="login-invite"
              v-model="inviteCode"
              type="text"
              autocomplete="off"
              class="login-input"
              :placeholder="i18n.t('login_invite_placeholder')"
            />
          </div>
          <div>
            <label for="login-password" class="login-label">{{ i18n.t('login_password') }}</label>
            <input
              id="login-password"
              v-model="password"
              type="password"
              :autocomplete="isRegister ? 'new-password' : 'current-password'"
              class="login-input"
              :placeholder="i18n.t('login_password_placeholder')"
            />
          </div>

          <p v-if="error" role="alert" class="text-sm text-red-700 dark:text-red-300">{{ error }}</p>
          <p v-if="notice" role="status" class="text-sm text-accent">{{ notice }}</p>

          <button
            type="submit"
            :disabled="loading"
            class="w-full rounded-xl bg-accent py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >{{ loading ? i18n.t('login_loading') : isRegister ? i18n.t('login_register') : i18n.t('login_login') }}</button>
        </form>

        <p class="mt-6 border-t border-border pt-5 text-center text-sm text-muted dark:border-gray-800 dark:text-gray-400">
          {{ isRegister ? i18n.t('login_have_account') : i18n.t('login_no_account') }}
          <button
            @click="isRegister = !isRegister; error = ''; notice = ''"
            class="text-accent hover:underline"
          >{{ isRegister ? i18n.t('login_login') : i18n.t('login_register') }}</button>
        </p>
      </section>
    </main>
  </div>
</template>

<style scoped>
.login-label {
  @apply mb-1.5 block text-sm font-medium text-ink dark:text-gray-200;
}

.login-input {
  @apply min-h-11 w-full rounded-xl border border-border bg-paper px-3 py-2 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 dark:placeholder:text-gray-400;
}
</style>
