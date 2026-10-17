<script setup lang="ts">
import { useI18nStore } from "../../stores/i18n"
import { useAuthStore } from "../../stores/auth"
import { useAdminUsersStore } from "../../stores/adminUsers"

const i18n = useI18nStore()
const auth = useAuthStore()
const usersStore = useAdminUsersStore()
</script>

<template>
<section class="space-y-6">
  <div class="flex items-center justify-between mb-4">
    <h2 class="text-lg font-semibold">{{ i18n.t('admin_user_management') }}</h2>
    <div class="flex items-center gap-2">
      <button
        @click="usersStore.showUserCreate = !usersStore.showUserCreate"
        class="px-4 py-2 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity"
      >{{ i18n.t('admin_create_user') }}</button>
      <button @click="usersStore.loadUsers" class="px-4 py-2 rounded border border-border dark:border-gray-700 text-sm hover:bg-surface transition-colors">{{ i18n.t('admin_refresh') }}</button>
    </div>
  </div>
  <p v-if="usersStore.userError" class="text-sm text-red-600 mb-3">{{ usersStore.userError }}</p>
  <div class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
    <div class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <h3 class="text-sm font-semibold mb-1">{{ i18n.t('admin_registration_approval') }}</h3>
        <p class="text-xs text-muted dark:text-gray-400">{{ i18n.t('admin_registration_approval_hint') }}</p>
      </div>
      <button
        @click="usersStore.toggleRegistrationApproval"
        class="text-xs px-3 py-1.5 rounded border"
        :class="usersStore.registrationApprovalEnabled ? 'bg-green-100 text-green-700 border-green-400 dark:bg-green-900 dark:text-green-300' : 'border-border dark:border-gray-700 text-muted dark:text-gray-400'"
      >{{ usersStore.registrationApprovalEnabled ? i18n.t('admin_enabled') : i18n.t('admin_disabled') }}</button>
    </div>
  </div>

  <div v-if="usersStore.showUserCreate" class="p-5 rounded-lg border border-border dark:border-gray-700 bg-surface dark:bg-gray-900">
    <h3 class="text-sm font-semibold mb-3">{{ i18n.t('admin_create_user') }}</h3>
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
      <input
        v-model="usersStore.userCreateForm.username"
        :placeholder="i18n.t('admin_username_placeholder')"
        class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
      />
      <input
        v-model="usersStore.userCreateForm.email"
        type="email"
        :placeholder="i18n.t('admin_email_placeholder')"
        class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
      />
      <input
        v-model="usersStore.userCreateForm.password"
        type="password"
        :placeholder="i18n.t('admin_password_placeholder')"
        class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
      />
      <select
        v-model="usersStore.userCreateForm.role"
        class="px-3 py-2 rounded border border-border dark:border-gray-700 text-sm bg-paper dark:bg-gray-800"
      >
        <option value="user">{{ i18n.t('admin_role_user') }}</option>
        <option value="admin">{{ i18n.t('admin_role_admin') }}</option>
        <option value="super_admin">{{ i18n.t('admin_role_super_admin') }}</option>
      </select>
    </div>
    <p v-if="usersStore.userCreateError" class="text-sm text-red-600 mt-2">{{ usersStore.userCreateError }}</p>
    <button
      @click="usersStore.createUser"
      :disabled="usersStore.userCreating"
      class="mt-3 px-4 py-2 rounded bg-accent text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
    >{{ usersStore.userCreating ? i18n.t('admin_saving') : i18n.t('admin_create_user') }}</button>
  </div>

  <div class="divide-y divide-border border border-border dark:border-gray-700 rounded-lg bg-surface dark:bg-gray-900">
    <div v-for="u in usersStore.users" :key="u.id" class="px-4 py-3 flex items-center justify-between flex-wrap gap-2">
      <div>
        <span class="text-sm font-medium">{{ u.username }}</span>
        <span class="text-xs text-muted dark:text-gray-400 ml-2">{{ u.email || '' }}</span>
        <span class="text-xs px-1.5 py-0.5 rounded-full ml-2" :class="u.role === 'super_admin' ? 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300' : u.role === 'admin' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'">{{ u.role === 'super_admin' ? i18n.t('admin_role_super_admin') : u.role === 'admin' ? i18n.t('admin_role_admin') : i18n.t('admin_role_user') }}</span>
        <span v-if="!u.approved" class="text-xs px-1.5 py-0.5 rounded-full ml-2 bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300">{{ i18n.t('admin_pending') }}</span>
        <span v-if="auth.isSuperAdmin && u.invite_tag" class="text-xs px-1.5 py-0.5 rounded-full ml-2 bg-cyan-100 text-cyan-700 dark:bg-cyan-900 dark:text-cyan-300">{{ i18n.t('admin_invite_tag', { tag: u.invite_tag }) }}</span>
      </div>
      <div class="flex items-center gap-2">
        <button @click="usersStore.toggleUserVisibility(u, 'r18_enabled')" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5" :class="u.r18_enabled ? 'text-purple-700 dark:text-purple-300 border-purple-500' : ''">
          {{ u.r18_enabled ? i18n.t('admin_r18_on') : i18n.t('admin_r18_off') }}
        </button>
        <button @click="usersStore.toggleUserVisibility(u, 'non_r18_enabled')" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5" :class="u.non_r18_enabled ? 'text-green-700 dark:text-green-300 border-green-500' : ''">
          {{ u.non_r18_enabled ? i18n.t('admin_all_ages_on') : i18n.t('admin_all_ages_off') }}
        </button>
        <button @click="usersStore.toggleUserVisibility(u, 'can_manage_visibility')" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5" :class="u.can_manage_visibility ? 'text-blue-700 dark:text-blue-300 border-blue-500' : ''">
          {{ u.can_manage_visibility ? i18n.t('admin_controls_on') : i18n.t('admin_controls_off') }}
        </button>
        <button v-if="!u.approved" @click="usersStore.approveUser(u.id)" class="text-xs px-2 py-1 rounded border border-green-500 text-green-600 hover:bg-green-50 dark:hover:bg-green-950">
          {{ i18n.t('admin_approve_user') }}
        </button>
        <button v-if="auth.isSuperAdmin" @click="usersStore.changeUserPassword(u)" :disabled="usersStore.passwordChanging[u.id]" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 hover:bg-accent/5 disabled:opacity-50">
          {{ i18n.t('admin_change_password') }}
        </button>
        <select @change="(e: any) => usersStore.changeUserRole(u.id, e.target.value)" class="text-xs px-2 py-1 rounded border border-border dark:border-gray-700 bg-paper dark:bg-gray-800">
          <option value="" disabled selected>{{ i18n.t('admin_change_role') }}</option>
          <option value="user">{{ i18n.t('admin_role_user') }}</option>
          <option value="admin">{{ i18n.t('admin_role_admin') }}</option>
          <option value="super_admin">{{ i18n.t('admin_role_super_admin') }}</option>
        </select>
        <button @click="usersStore.deleteUser(u.id, u.username)" class="text-xs text-red-500 hover:text-red-700">{{ u.approved ? i18n.t('admin_delete') : i18n.t('admin_reject_user') }}</button>
      </div>
    </div>
    <p v-if="usersStore.users.length === 0" class="px-4 py-3 text-sm text-muted dark:text-gray-400">{{ i18n.t('admin_no_users') }}</p>
  </div>
</section>
</template>
