import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"

const TERMINAL = ["completed", "failed", "cancelled", "completed_with_errors"]

export const useCrawlStore = defineStore("crawl", () => {
  const activeTask = ref<any | null>(null)
  const error = ref("")
  const loading = ref(false)
  let timer: number | null = null

  async function refresh(taskId: string) {
    try {
      activeTask.value = await api.get<any>(`/crawl/tasks/${taskId}`)
      if (TERMINAL.includes(activeTask.value.status)) {
        stopPolling()
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : "Failed to load crawl task"
      stopPolling()
    }
  }

  async function startPolling(taskId: string) {
    if (timer !== null) return
    await refresh(taskId)
    if (!activeTask.value || TERMINAL.includes(activeTask.value.status)) {
      return
    }
    timer = window.setInterval(() => refresh(taskId), 2000)
  }

  function stopPolling() {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  async function setTask(task: any) {
    if (activeTask.value?.id && activeTask.value.id !== task?.id) {
      stopPolling()
    }
    activeTask.value = task
    error.value = ""
    if (task?.id && !TERMINAL.includes(task.status)) {
      await startPolling(task.id)
    }
  }

  async function pauseTask() {
    if (!activeTask.value?.id) return
    activeTask.value = await api.post(`/crawl/tasks/${activeTask.value.id}/pause`)
  }

  async function resumeTask() {
    if (!activeTask.value?.id) return
    activeTask.value = await api.post(`/crawl/tasks/${activeTask.value.id}/resume`)
  }

  async function cancelTask() {
    if (!activeTask.value?.id) return
    activeTask.value = await api.post(`/crawl/tasks/${activeTask.value.id}/cancel`)
    stopPolling()
  }

  function clear() {
    activeTask.value = null
    error.value = ""
    stopPolling()
  }

  return {
    activeTask,
    error,
    loading,
    setTask,
    startPolling,
    pauseTask,
    resumeTask,
    cancelTask,
    clear,
  }
})
