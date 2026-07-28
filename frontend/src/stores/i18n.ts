import { defineStore } from "pinia"
import { ref, computed } from "vue"

type Locale = "zh" | "en"

const messages: Record<Locale, Record<string, string>> = {
  zh: {
    nav_search: "搜索",
    nav_admin: "管理",
    nav_login: "登录",
    nav_logout: "退出",
    home_library: "书架",
    home_continue: "继续阅读",
    home_loading: "加载中...",
    home_empty: "书架是空的",
    home_empty_hint: "创建一个书源并触发同步来导入第一本书。",
    home_goto_admin: "前往管理",
    home_reading: "阅读中",
    home_completed: "已完结",
    home_ongoing: "连载中",
    home_no_desc: "暂无简介",
    home_delete_confirm: "确定删除《{title}》？此操作不可撤销。",
    home_delete_failed: "删除失败",
    home_delete_title: "删除这本书",
    home_books_count: "{n} 本",
    admin_title: "管理",
    admin_tab_sources: "书源",
    admin_tab_cookies: "Cookie",
    admin_tab_sync: "同步",
    admin_tab_logs: "日志",
    admin_tab_tokens: "令牌",
    admin_tab_status: "状态",
    admin_tab_index: "索引",
    admin_tab_yuedu: "书源导入",
    admin_tab_creds: "账号密码",
    admin_tab_users: "用户管理",
    admin_tab_approvals: "待审批",
    admin_add_source: "添加书源",
    admin_create_source: "创建书源",
    admin_no_sources: "暂无书源",
    admin_enabled: "已启用",
    admin_disabled: "已禁用",
    admin_delete: "删除",
    admin_refresh: "刷新",
    admin_create: "创建",
    admin_placeholder_id: "Source ID（如 alicesw）",
    admin_placeholder_name: "显示名称",
    admin_placeholder_url: "基础 URL",
    admin_placeholder_plugin: "插件名称",
    admin_placeholder_source: "Source ID",
    admin_placeholder_cookie: "Cookie 文本...",
    admin_placeholder_book_url: "要同步的书籍 URL",
    admin_placeholder_token_name: "令牌名称",
    admin_placeholder_days: "天数（可选）",
    admin_placeholder_username: "用户名",
    admin_placeholder_password: "密码",
  },
  en: {
    nav_search: "Search",
    nav_admin: "Admin",
    nav_login: "Login",
    nav_logout: "Logout",
    home_library: "Library",
    home_continue: "Continue Reading",
    home_loading: "Loading...",
    home_empty: "Your library is empty",
    home_empty_hint: "Create a source and trigger a sync to import your first book.",
    home_goto_admin: "Go to Admin",
    home_reading: "Reading",
    home_completed: "Completed",
    home_ongoing: "Ongoing",
    home_no_desc: "No description",
    home_delete_confirm: 'Delete "{title}"? This cannot be undone.',
    home_delete_failed: "Delete failed",
    home_delete_title: "Delete book",
    home_books_count: "{n} books",
    admin_title: "Admin",
    admin_tab_sources: "Sources",
    admin_tab_cookies: "Cookies",
    admin_tab_sync: "Sync",
    admin_tab_logs: "Logs",
    admin_tab_tokens: "Tokens",
    admin_tab_status: "Status",
    admin_tab_index: "Index",
    admin_tab_yuedu: "YueDu Import",
    admin_tab_creds: "Credentials",
    admin_tab_users: "Users",
    admin_tab_approvals: "Approvals",
    admin_add_source: "Add Source",
    admin_create_source: "Create Source",
    admin_no_sources: "No sources configured.",
    admin_enabled: "enabled",
    admin_disabled: "disabled",
    admin_delete: "Delete",
    admin_refresh: "Refresh",
    admin_create: "Create",
    admin_placeholder_id: "Source ID (e.g. alicesw)",
    admin_placeholder_name: "Display name",
    admin_placeholder_url: "Base URL",
    admin_placeholder_plugin: "Plugin name",
    admin_placeholder_source: "Source ID",
    admin_placeholder_cookie: "Cookie string...",
    admin_placeholder_book_url: "Book URL to sync",
    admin_placeholder_token_name: "Token name",
    admin_placeholder_days: "Days (optional)",
    admin_placeholder_username: "Username",
    admin_placeholder_password: "Password",
  },
}

export const useI18nStore = defineStore("i18n", () => {
  const saved = localStorage.getItem("novelhub_locale")
  const locale = ref<Locale>((saved === "en" ? "en" : "zh") as Locale)

  function t(key: string, params?: Record<string, string | number>): string {
    const msg = messages[locale.value][key]
    if (!msg) return key
    if (!params) return msg
    return msg.replace(/\{(\w+)\}/g, (_, k) => String(params[k] ?? `{${k}}`))
  }

  function setLocale(l: Locale) {
    locale.value = l
    localStorage.setItem("novelhub_locale", l)
  }

  function toggleLocale() {
    setLocale(locale.value === "zh" ? "en" : "zh")
  }

  const isZh = computed(() => locale.value === "zh")

  return { locale, t, setLocale, toggleLocale, isZh }
})
