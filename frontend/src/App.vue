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

type Book = {
  id: string
  title: string
  description?: string | null
  status?: string | null
}

const books = ref<Book[]>([])
const loading = ref(true)
const error = ref("")
const apiStatus = ref("checking")

onMounted(async () => {
  try {
    const healthResponse = await fetch("/api/test")
    apiStatus.value = healthResponse.ok ? "ok" : "failed"

    const response = await fetch("/api/books")
    if (!response.ok) {
      throw new Error(`API returned ${response.status}`)
    }
    const contentType = response.headers.get("content-type") || ""
    if (!contentType.includes("application/json")) {
      throw new Error("API did not return JSON. Check proxy routing.")
    }
    books.value = await response.json()
  } catch (err) {
    apiStatus.value = "failed"
    error.value = err instanceof Error ? err.message : "Unknown error"
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">Personal Novel Library</p>
      <h1>NovelHub</h1>
      <p class="subtitle">
        一个面向 NAS 长期运行的个人小说数字图书馆。
      </p>
      <p class="status" :class="apiStatus">
        API: {{ apiStatus }}
      </p>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>书库</h2>
        <span>{{ books.length }} 本</span>
      </div>

      <p v-if="loading">正在加载书库……</p>
      <p v-else-if="error" class="error">API 连接失败：{{ error }}</p>
      <p v-else-if="books.length === 0" class="empty">
        暂无小说。可以先创建来源，再调用同步接口导入本地 Markdown 小说。
      </p>
      <article v-for="book in books" :key="book.id" class="book-card">
        <h3>{{ book.title }}</h3>
        <p>{{ book.description || "暂无简介" }}</p>
        <small>{{ book.status || "unknown" }}</small>
      </article>
    </section>
  </main>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  padding: 48px;
  color: #172033;
  background: linear-gradient(135deg, #f8f4ea, #edf5ff);
  font-family:
    Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
    sans-serif;
}

.hero {
  max-width: 760px;
  margin-bottom: 32px;
}

.eyebrow {
  color: #7c5c2e;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  font-size: 64px;
}

.subtitle {
  font-size: 20px;
  color: #526071;
}

.status {
  display: inline-flex;
  padding: 6px 12px;
  border-radius: 999px;
  background: #fff;
  color: #526071;
  font-size: 14px;
}

.status.ok {
  color: #067647;
}

.status.failed {
  color: #b42318;
}

.panel {
  max-width: 860px;
  padding: 24px;
  border: 1px solid rgba(23, 32, 51, 0.12);
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.72);
  box-shadow: 0 20px 60px rgba(23, 32, 51, 0.08);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.book-card {
  padding: 16px;
  border-top: 1px solid rgba(23, 32, 51, 0.1);
}

.book-card h3 {
  margin: 0 0 8px;
}

.empty {
  color: #526071;
}

.error {
  color: #b42318;
}
</style>
