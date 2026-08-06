import { createRouter, createWebHistory } from "vue-router"
import { useAuthStore } from "../stores/auth"

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "home",
      component: () => import("../pages/HomePage.vue"),
    },
    {
      path: "/books",
      name: "books",
      component: () => import("../pages/BooksPage.vue"),
    },
    {
      path: "/books/:id",
      name: "book-detail",
      component: () => import("../pages/BookDetailPage.vue"),
    },
    {
      path: "/books/:bookId/chapters/:chapterId",
      name: "reader",
      component: () => import("../pages/ReaderPage.vue"),
    },
    {
      path: "/search",
      name: "search",
      component: () => import("../pages/SearchPage.vue"),
    },
    {
      path: "/sync",
      name: "sync",
      component: () => import("../pages/SyncPage.vue"),
    },
    {
      path: "/login",
      name: "login",
      component: () => import("../pages/LoginPage.vue"),
    },
    {
      path: "/admin",
      name: "admin",
      component: () => import("../pages/AdminPage.vue"),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
  ],
})

router.beforeEach(async (to, _from, next) => {
  const auth = useAuthStore()
  const token = localStorage.getItem("novelhub_token")
  if (to.name === "login" && token) {
    next({ name: "home" })
  } else if (to.name !== "login" && !token) {
    next({ name: "login" })
  } else {
    if (to.meta.requiresAdmin) {
      if (!auth.user) {
        await auth.fetchMe()
      }
      if (!auth.isAdmin) {
        next({ name: "home" })
        return
      }
    }
    next()
  }
})

export default router
