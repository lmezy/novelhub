import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"

// Polling can only learn something new while the worker is still moving the
// task.  A paused task changes when the user resumes it, which updates this
// store directly, so polling it forever only kept the API -- and one pooled
// database connection every two seconds -- busy for nothing.
const POLLABLE = ["pending", "running"]

export const useCrawlStore = defineStore("crawl", () => {
  const activeTask = ref<any | null>(null)
  const error = ref("")
  const loading = ref(false)
  let timer: number | null = null

  let pollVersion = 0
  let pollingId: string | null = null

  function stopPolling() {
    pollVersion++
    pollingId = null
    if (timer !== null) window.clearTimeout(timer)
    timer = null
  }

  async function startPolling(taskId: string) {
    if (pollingId === taskId) return
    stopPolling()
    pollingId = taskId
    const version = pollVersion
    async function refresh() {
      try {
        const task = await api.get<any>(`/crawl/tasks/${taskId}`)
        if (version !== pollVersion) return
        activeTask.value = task
        if (POLLABLE.includes(task.status)) {
          timer = window.setTimeout(refresh, 2000)
        } else {
          stopPolling()
        }
      } catch (e) {
        if (version !== pollVersion) return
        error.value = e instanceof Error ? e.message : "Failed to load crawl task"
        stopPolling()
      }
    }
    await refresh()
  }

  async function setTask(task: any) {
    stopPolling()
    activeTask.value = task
    error.value = ""
    if (task?.id && POLLABLE.includes(task.status)) await startPolling(task.id)
  }

  async function changeStatus(action: "pause" | "resume" | "cancel") {
    const id = activeTask.value?.id
    if (!id) return
    const update = await api.post<any>(`/crawl/tasks/${id}/${action}`)
    if (activeTask.value?.id === id) await setTask({ ...activeTask.value, ...update })
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
    pauseTask: () => changeStatus("pause"),
    resumeTask: () => changeStatus("resume"),
    cancelTask: () => changeStatus("cancel"),
    clear,
  }
})
