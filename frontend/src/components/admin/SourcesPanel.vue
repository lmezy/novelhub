<script setup lang="ts">
import { useI18nStore } from "../../stores/i18n"
import { useAuthStore } from "../../stores/auth"
import { useAdminSourcesStore, type Source } from "../../stores/adminSources"
import { useAdminCookiesStore } from "../../stores/adminCookies"
import { useAdminCredsStore } from "../../stores/adminCreds"

const i18n = useI18nStore()
const auth = useAuthStore()
const sourcesStore = useAdminSourcesStore()
const cookiesStore = useAdminCookiesStore()
const credsStore = useAdminCredsStore()

function toggleSourceDetails(s: Source) {
  if (sourcesStore.expandedSourceId === s.id) {
    sourcesStore.expandedSourceId = ""
    return
  }
  sourcesStore.expandedSourceId = s.id
  // Prefill from the stored Cookie: the panel sat next to an existing Cookie
  // while showing an empty expiry field, so saving replaced the value *and*
  // silently nulled ``expired_at`` nobody had touched.
  const existing = cookiesStore.sourceCookies(s.id)[0]
  cookiesStore.cookieForm = {
    source: s.id,
    cookie_data: "",
    expired_at: cookiesStore.toDateTimeLocal(existing?.expired_at),
  }
  cookiesStore.cookieEditingId = ""
  cookiesStore.cookieError = ""
  cookiesStore.cookieTestResult = null
  cookiesStore.cookieTestError = ""
  credsStore.credForm = { source: s.id, username: "", password: "" }
  credsStore.credError = ""
}

async function saveSourceCookie(s: Source) {
  cookiesStore.cookieForm.source = s.id
  const existing = cookiesStore.sourceCookies(s.id)[0]
  if (existing) {
    cookiesStore.cookieEditingId = existing.id
  }
  await cookiesStore.createCookie()
}

async function testSourceCookie(s: Source) {
  cookiesStore.cookieForm.source = s.id
  await cookiesStore.testCookie()
}

async function saveSourceCred(s: Source) {
  credsStore.credForm.source = s.id
  await credsStore.createCred()
}
</script>

<template>
<section class="space-y-6">
  <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
    <h2 class="text-sm font-semibold mb-4">{{ i18n.t('admin_add_source') }}</h2>
    <div class="grid grid-cols-2 gap-3 mb-3">
      <input v-model="sourcesStore.sourceForm.id" :disabled="!!sourcesStore.sourceEditingId" :placeholder="i18n.t('admin_placeholder_id')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800 disabled:opacity-60" />
      <input v-model="sourcesStore.sourceForm.name" :placeholder="i18n.t('admin_placeholder_name')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      <input v-model="sourcesStore.sourceForm.url" :placeholder="i18n.t('admin_placeholder_url')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
      <input v-model="sourcesStore.sourceForm.plugin_name" :placeholder="i18n.t('admin_placeholder_plugin')" class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800" />
    </div>
    <div class="flex flex-wrap items-center gap-4 mb-3">
      <label v-if="auth.isAdmin" class="inline-flex items-center gap-2 text-xs text-muted dark:text-gray-400">
        <span>{{ i18n.t('admin_scope_label') }}</span>
        <select v-model="sourcesStore.sourceForm.scope" class="px-2 py-1 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800">
          <option value="personal">{{ i18n.t('admin_source_personal') }}</option>
          <option value="global">{{ i18n.t('admin_source_global') }}</option>
        </select>
      </label>
      <label class="inline-flex items-center gap-2 text-xs text-muted dark:text-gray-400 cursor-pointer">
        <input type="checkbox" v-model="sourcesStore.sourceForm.enabled" class="rounded" />
        {{ i18n.t('admin_source_enabled') }}
      </label>
      <label class="inline-flex items-center gap-2 text-xs text-muted dark:text-gray-400 cursor-pointer">
        <input type="checkbox" v-model="sourcesStore.sourceForm.is_r18" class="rounded" />
        {{ i18n.t('admin_r18_label') }}
      </label>
    </div>
    <div class="flex flex-wrap items-center gap-4 mb-3">
      <label class="text-xs text-muted dark:text-gray-400">
        <span class="block mb-1">{{ i18n.t('admin_sync_interval_label') }}</span>
        <input
          v-model="sourcesStore.sourceForm.sync_interval_seconds"
          type="number"
          min="0"
          max="3600"
          step="1"
          :placeholder="i18n.t('admin_sync_interval_placeholder')"
          class="w-32 px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
        />
      </label>
      <p class="text-xs text-muted dark:text-gray-400 max-w-xl leading-relaxed">{{ i18n.t('admin_sync_interval_hint') }}</p>
    </div>
    <textarea
      v-model="sourcesStore.sourceConfigText"
      :placeholder="i18n.t('admin_source_config_placeholder')"
      rows="6"
      class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs font-mono bg-paper dark:bg-gray-800 resize-y mb-3"
    />
    <p v-if="sourcesStore.sourceError" class="text-sm text-red-600 mb-2">{{ sourcesStore.sourceError }}</p>
    <div class="flex items-center gap-2">
      <button @click="sourcesStore.createSource" class="px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90">
        {{ sourcesStore.sourceEditingId ? i18n.t('admin_save') : i18n.t('admin_create_source') }}
      </button>
      <button v-if="sourcesStore.sourceEditingId" @click="sourcesStore.resetSourceForm" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-accent/5">{{ i18n.t('admin_cancel') }}</button>
    </div>
  </div>

  <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
    <div v-for="s in sourcesStore.sources" :key="s.id" class="px-4 py-3">
      <div class="flex items-center justify-between flex-wrap gap-2">
        <div>
          <span class="text-sm font-medium">{{ s.name }}</span>
          <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ s.id }} ({{ s.plugin_name }})</span>
          <span v-if="s.owner_id" class="text-xs px-1.5 py-0.5 rounded ml-2 bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300">{{ i18n.t('admin_source_personal') }}</span>
          <span v-else class="text-xs px-1.5 py-0.5 rounded ml-2 bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300">{{ i18n.t('admin_source_global') }}</span>
          <span v-if="s.is_r18" class="text-xs px-1.5 py-0.5 rounded ml-2 bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300">R18</span>
          <span
            v-if="s.sync_interval_seconds != null"
            class="text-xs px-1.5 py-0.5 rounded ml-2 bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-200"
          >{{ s.sync_interval_seconds > 0 ? i18n.t('admin_sync_interval_badge', { seconds: s.sync_interval_seconds }) : i18n.t('admin_sync_interval_off') }}</span>
          <span
            v-if="cookiesStore.sourceCookies(s.id).length"
            class="text-xs px-1.5 py-0.5 rounded ml-2"
            :class="cookiesStore.cookieExpired(cookiesStore.sourceCookies(s.id)[0]) ? 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300' : 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'"
          >{{ cookiesStore.cookieExpired(cookiesStore.sourceCookies(s.id)[0]) ? i18n.t('admin_cookie_expired') : i18n.t('admin_cookie_saved') }}</span>
          <span v-else class="text-xs px-1.5 py-0.5 rounded ml-2 bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">{{ i18n.t('admin_cookie_not_saved') }}</span>
          <span
            v-if="!s.owner_id && s.show_contributor && s.submitter_username"
            class="text-xs text-muted dark:text-gray-400 ml-2"
          >{{ i18n.t('admin_source_contributor', { name: s.submitter_username }) }}</span>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs" :class="s.enabled ? 'text-green-600' : 'text-red-500'">{{ s.enabled ? i18n.t('admin_enabled') : i18n.t('admin_disabled') }}</span>
          <button v-if="sourcesStore.canManageSource(s)" @click="toggleSourceDetails(s)" class="text-xs text-accent hover:underline">
            {{ i18n.t('admin_tab_cookies') }} / {{ i18n.t('admin_tab_creds') }}
          </button>
          <button v-if="sourcesStore.canManageSource(s)" @click="sourcesStore.editSource(s)" class="text-xs text-accent hover:underline">{{ i18n.t('admin_edit') }}</button>
          <button v-if="sourcesStore.canManageSource(s)" @click="sourcesStore.deleteSource(s.id)" class="text-xs text-red-500 hover:text-red-700">{{ i18n.t('admin_delete') }}</button>
        </div>
      </div>

      <div v-if="sourcesStore.expandedSourceId === s.id && sourcesStore.canManageSource(s)" class="mt-4 grid gap-4 lg:grid-cols-2">
        <div class="rounded-lg border border-border dark:border-gray-700 p-3">
          <h3 class="text-xs font-semibold mb-3">{{ i18n.t('admin_tab_cookies') }}</h3>
          <div v-if="cookiesStore.sourceCookies(s.id).length" class="space-y-2 mb-3">
            <div
              v-for="c in cookiesStore.sourceCookies(s.id)"
              :key="c.id"
              class="flex items-center justify-between gap-2 text-xs"
            >
              <div class="min-w-0">
                <div class="truncate">{{ c.source }}</div>
                <div v-if="cookiesStore.cookieWrittenAt(c)" class="text-muted dark:text-gray-400 text-[11px]">
                  {{ i18n.t('admin_cookie_written_at') }} {{ new Date(cookiesStore.cookieWrittenAt(c)).toLocaleString() }}
                </div>
              </div>
              <div class="flex items-center gap-2">
                <button @click="cookiesStore.editCookie(c)" class="text-accent hover:underline">{{ i18n.t('admin_edit') }}</button>
                <button @click="cookiesStore.deleteCookie(c.id)" class="text-red-500 hover:underline">{{ i18n.t('admin_delete') }}</button>
              </div>
            </div>
          </div>
          <p v-else class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_no_cookies') }}</p>
          <p
            v-if="cookiesStore.sourceCookies(s.id).length && cookiesStore.cookieExpired(cookiesStore.sourceCookies(s.id)[0])"
            class="text-xs text-red-600 mb-2"
          >{{ i18n.t('admin_cookie_expired_hint') }}</p>
          <textarea
            v-model="cookiesStore.cookieForm.cookie_data"
            :placeholder="i18n.t('admin_placeholder_cookie')"
            rows="3"
            class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs font-mono bg-paper dark:bg-gray-800 resize-y mb-2"
          />
          <input
            v-model="cookiesStore.cookieForm.expired_at"
            type="datetime-local"
            class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800 mb-2"
          />
          <div class="flex gap-2">
            <button @click="saveSourceCookie(s)" class="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium">
              {{ cookiesStore.sourceCookies(s.id).length ? i18n.t('admin_update_cookie') : i18n.t('admin_save_cookie') }}
            </button>
            <button @click="testSourceCookie(s)" :disabled="cookiesStore.cookieTesting" class="px-3 py-1.5 rounded border border-accent text-accent text-xs font-medium disabled:opacity-50">
              {{ cookiesStore.cookieTesting ? i18n.t('admin_testing') : i18n.t('admin_test_cookie') }}
            </button>
          </div>
          <p v-if="cookiesStore.cookieError" class="text-xs text-red-600 mt-2">{{ cookiesStore.cookieError }}</p>
          <div v-if="cookiesStore.cookieTestResult" class="mt-2 text-xs text-green-700 dark:text-green-400">{{ cookiesStore.cookieTestResult.message }}</div>
          <p v-if="cookiesStore.cookieTestError" class="text-xs text-red-600 mt-2">{{ cookiesStore.cookieTestError }}</p>
        </div>

        <div class="rounded-lg border border-border dark:border-gray-700 p-3">
          <h3 class="text-xs font-semibold mb-3">{{ i18n.t('admin_tab_creds') }}</h3>
          <div v-if="credsStore.sourceCreds(s.id).length" class="space-y-2 mb-3">
            <div
              v-for="c in credsStore.sourceCreds(s.id)"
              :key="c.id"
              class="flex items-center justify-between gap-2 text-xs"
            >
              <span>{{ c.username }}</span>
              <div class="flex items-center gap-2">
                <button @click="credsStore.autoLogin(c.id)" :disabled="credsStore.credLoggingIn[c.id]" class="text-green-600 hover:underline disabled:opacity-50">
                  {{ credsStore.credLoggingIn[c.id] ? i18n.t('admin_logging_in') : i18n.t('admin_auto_login') }}
                </button>
                <button @click="credsStore.startManualLogin(c.id)" :disabled="credsStore.manualLoginLoading" class="text-blue-600 hover:underline disabled:opacity-50">
                  {{ i18n.t('admin_manual_login') }}
                </button>
                <button @click="credsStore.deleteCred(c.id)" class="text-red-500 hover:underline">{{ i18n.t('admin_delete') }}</button>
              </div>
              <p v-if="credsStore.credLoginResult[c.id]" class="w-full text-green-700 dark:text-green-400">{{ credsStore.credLoginResult[c.id].message }}</p>
              <p v-if="credsStore.credLoginError[c.id]" class="w-full text-red-600">{{ credsStore.credLoginError[c.id] }}</p>
            </div>
          </div>
          <p v-else class="text-xs text-muted dark:text-gray-400 mb-3">{{ i18n.t('admin_no_creds') }}</p>
          <input
            v-model="credsStore.credForm.username"
            :placeholder="i18n.t('admin_placeholder_username')"
            class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800 mb-2"
          />
          <input
            v-model="credsStore.credForm.password"
            type="password"
            :placeholder="i18n.t('admin_placeholder_password')"
            class="w-full px-3 py-2 rounded border border-border dark:border-gray-700 text-xs bg-paper dark:bg-gray-800 mb-2"
          />
          <button @click="saveSourceCred(s)" class="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium">{{ i18n.t('admin_save_cred') }}</button>
          <p v-if="credsStore.credError" class="text-xs text-red-600 mt-2">{{ credsStore.credError }}</p>
        </div>
      </div>
    </div>
    <p v-if="sourcesStore.sources.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_sources') }}</p>
  </div>
</section>
</template>
